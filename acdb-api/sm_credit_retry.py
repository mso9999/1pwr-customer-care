"""Durable retry queue for CC -> SparkMeter credit pushes."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from sparkmeter_credit import credit_sparkmeter

logger = logging.getLogger("cc-api.sm-credit-retry")

MAX_RETRY_BACKOFF_SECONDS = 3600
FINAL_FAIL_ATTEMPTS = 20
BLOCKED_UNCOMMISSIONED_PREFIX = "blocked_uncommissioned"
THUNDERCLOUD_SITES = {"MAK", "LAB"}


def _next_retry_seconds(attempt_count: int) -> int:
    # 1m, 2m, 4m, ... capped at 60m
    if attempt_count < 0:
        attempt_count = 0
    return min(60 * (2 ** attempt_count), MAX_RETRY_BACKOFF_SECONDS)


def _site_from_account(account_number: str) -> str:
    m = re.search(r"([A-Z]{3})$", (account_number or "").upper())
    return m.group(1) if m else ""


def enqueue_sm_credit_retry(
    *,
    account_number: str,
    amount: float,
    memo: str,
    external_id: str | None,
    error: str | None = None,
    status: str = "pending",
) -> None:
    """Insert or refresh a pending credit retry row."""
    from customer_api import get_connection

    status_norm = "failed" if status == "failed" else "pending"
    ext_id = (external_id or "").strip()
    with get_connection() as conn:
        cur = conn.cursor()
        # Do not rely on ON CONFLICT(external_id): some environments may have
        # only a partial index for external_id, which cannot be used as an
        # upsert arbiter. Use update-then-insert for schema compatibility.
        if ext_id:
            cur.execute(
                """
                UPDATE sm_credit_retry_queue
                SET account_number = %s,
                    amount = %s,
                    memo = %s,
                    status = %s,
                    last_error = %s,
                    next_retry_at = NOW(),
                    resolved_at = NULL
                WHERE external_id = %s
                """,
                (
                    account_number,
                    float(amount),
                    memo or "",
                    status_norm,
                    error,
                    ext_id,
                ),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO sm_credit_retry_queue
                        (account_number, amount, memo, external_id, status,
                         first_error, last_error, next_retry_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        account_number,
                        float(amount),
                        memo or "",
                        ext_id,
                        status_norm,
                        error,
                        error,
                    ),
                )
        else:
            cur.execute(
                """
                INSERT INTO sm_credit_retry_queue
                    (account_number, amount, memo, external_id, status,
                     first_error, last_error, next_retry_at)
                VALUES (%s, %s, %s, NULL, %s, %s, %s, NOW())
                """,
                (
                    account_number,
                    float(amount),
                    memo or "",
                    status_norm,
                    error,
                    error,
                ),
            )
        conn.commit()


def _account_credit_eligibility(cur, account_number: str) -> tuple[bool, str | None]:
    """Return whether account is eligible for upstream credit push."""
    cur.execute(
        """
        SELECT
            COALESCE(c.customer_commissioned, FALSE) AS commissioned,
            c.date_service_connected IS NOT NULL     AS has_connected_date,
            EXISTS(
                SELECT 1
                FROM meters m
                WHERE m.account_number = a.account_number
            ) AS has_any_meter,
            (
                SELECT m2.meter_id
                FROM meters m2
                WHERE m2.account_number = a.account_number
                ORDER BY m2.updated_at DESC NULLS LAST,
                         m2.created_at DESC NULLS LAST,
                         m2.id DESC
                LIMIT 1
            ) AS latest_meter_id
        FROM accounts a
        LEFT JOIN customers c ON c.id = a.customer_id
        WHERE a.account_number = %s
        LIMIT 1
        """,
        (account_number,),
    )
    row = cur.fetchone()
    if not row:
        return True, None
    commissioned = bool(row[0])
    has_connected_date = bool(row[1])
    has_any_meter = bool(row[2])
    latest_meter_id = str(row[3] or "").strip().upper()
    if not commissioned or not has_connected_date:
        return False, "customer_not_commissioned"
    # Koios credits by customer_code and can succeed without a valid meter serial
    # on the account row. ThunderCloud requires an actual meter/customer mapping.
    site = _site_from_account(account_number)
    if site in THUNDERCLOUD_SITES:
        if not has_any_meter:
            return False, "no_meter_assigned"
        if latest_meter_id.startswith("ACCT-"):
            return False, "meter_serial_placeholder"
    return True, None


def _release_blocked_retries(cur, limit: int = 200) -> int:
    """Re-open blocked rows once account commissioning state is eligible."""
    cur.execute(
        """
        SELECT id, account_number
        FROM sm_credit_retry_queue
        WHERE status = 'failed'
          AND COALESCE(last_error, '') LIKE %s
        ORDER BY id ASC
        LIMIT %s
        FOR UPDATE SKIP LOCKED
        """,
        (f"{BLOCKED_UNCOMMISSIONED_PREFIX}:%", max(1, int(limit))),
    )
    rows: List[Tuple[Any, ...]] = list(cur.fetchall())
    reopened = 0
    for q_id, account in rows:
        eligible, _ = _account_credit_eligibility(cur, str(account))
        if not eligible:
            continue
        cur.execute(
            """
            UPDATE sm_credit_retry_queue
            SET status = 'pending',
                last_error = NULL,
                next_retry_at = NOW(),
                resolved_at = NULL
            WHERE id = %s
            """,
            (int(q_id),),
        )
        reopened += 1
    return reopened


def process_due_sm_credit_retries(limit: int = 20) -> Dict[str, int]:
    """Retry due rows from ``sm_credit_retry_queue`` and return counters."""
    from customer_api import get_connection

    processed = 0
    ok = 0
    failed = 0
    blocked = 0

    with get_connection() as conn:
        cur = conn.cursor()
        reopened = _release_blocked_retries(cur)
        cur.execute(
            """
            SELECT id, account_number, amount, memo, COALESCE(external_id, ''), attempt_count
            FROM sm_credit_retry_queue
            WHERE status IN ('pending', 'retrying')
              AND next_retry_at <= NOW()
            ORDER BY next_retry_at ASC, id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (max(1, int(limit)),),
        )
        rows: List[Tuple[Any, ...]] = list(cur.fetchall())

        for row in rows:
            q_id = int(row[0])
            account = str(row[1])
            amount = float(row[2])
            memo = str(row[3] or "")
            external_id = str(row[4] or "")
            attempts = int(row[5] or 0)
            processed += 1

            eligible, reason = _account_credit_eligibility(cur, account)
            if not eligible:
                blocked += 1
                err = f"{BLOCKED_UNCOMMISSIONED_PREFIX}:{reason or 'ineligible'}"
                cur.execute(
                    """
                    UPDATE sm_credit_retry_queue
                    SET status = 'failed',
                        last_error = %s,
                        first_error = COALESCE(first_error, %s),
                        last_attempt_at = NOW(),
                        next_retry_at = NOW()
                    WHERE id = %s
                    """,
                    (err, err, q_id),
                )
                continue

            result = credit_sparkmeter(account, amount, memo, external_id)
            err_text = (result.error or "").strip().lower()
            duplicate_external = "external_id has already been taken" in err_text
            if result.success:
                cur.execute(
                    """
                    UPDATE sm_credit_retry_queue
                    SET status = 'done',
                        resolved_at = NOW(),
                        last_attempt_at = NOW(),
                        attempt_count = %s,
                        last_error = NULL
                    WHERE id = %s
                    """,
                    (attempts + 1, q_id),
                )
                ok += 1
                continue
            if duplicate_external:
                # Upstream already has this credit idempotency key. Treat this as
                # terminal success to avoid endless retries for already-applied credits.
                cur.execute(
                    """
                    UPDATE sm_credit_retry_queue
                    SET status = 'done',
                        resolved_at = NOW(),
                        last_attempt_at = NOW(),
                        attempt_count = %s,
                        last_error = NULL
                    WHERE id = %s
                    """,
                    (attempts + 1, q_id),
                )
                ok += 1
                logger.info(
                    "SM credit retry idempotent duplicate treated done id=%s acct=%s ext=%s",
                    q_id, account, external_id or "-",
                )
                continue

            failed += 1
            next_attempts = attempts + 1
            backoff = _next_retry_seconds(attempts)
            next_status = "failed" if next_attempts >= FINAL_FAIL_ATTEMPTS else "retrying"
            err = (result.error or "unknown credit error").strip()[:800]
            cur.execute(
                """
                UPDATE sm_credit_retry_queue
                SET status = %s,
                    attempt_count = %s,
                    last_error = %s,
                    first_error = COALESCE(first_error, %s),
                    last_attempt_at = NOW(),
                    next_retry_at = NOW() + (%s * INTERVAL '1 second')
                WHERE id = %s
                """,
                (next_status, next_attempts, err, err, backoff, q_id),
            )
            logger.warning(
                "SM credit retry failed id=%s acct=%s amount=%.2f attempts=%s: %s",
                q_id, account, amount, next_attempts, err,
            )

        conn.commit()

    return {
        "processed": processed,
        "ok": ok,
        "failed": failed,
        "blocked": blocked,
        "reopened": reopened,
    }


def credit_sm_with_retry(
    *,
    account_number: str,
    amount: float,
    memo: str,
    external_id: str | None,
    replay_due_limit: int = 3,
) -> Dict[str, Any]:
    """Try immediate credit; on failure queue for durable retry."""
    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        eligible, reason = _account_credit_eligibility(cur, account_number)
    if not eligible:
        err = f"{BLOCKED_UNCOMMISSIONED_PREFIX}:{reason or 'ineligible'}"
        enqueue_sm_credit_retry(
            account_number=account_number,
            amount=amount,
            memo=memo,
            external_id=external_id,
            error=err,
            status="failed",
        )
        return {
            "success": False,
            "platform": "deferred",
            "queued_retry": True,
            "deferred_until_commissioned": True,
            "error": err,
        }

    result = credit_sparkmeter(account_number, amount, memo, external_id or "")
    summary: Dict[str, Any] = {
        "success": bool(result.success),
        "platform": result.platform,
        "queued_retry": False,
    }
    if result.sm_transaction_id:
        summary["sm_transaction_id"] = result.sm_transaction_id
    if result.error:
        summary["error"] = result.error

    if result.success:
        # Opportunistically drain a few old retries whenever the path is healthy.
        if replay_due_limit > 0:
            replay = process_due_sm_credit_retries(limit=replay_due_limit)
            summary["retry_replay"] = replay
        return summary

    err = (result.error or "unknown credit error").strip()
    enqueue_sm_credit_retry(
        account_number=account_number,
        amount=amount,
        memo=memo,
        external_id=external_id,
        error=err,
        status="pending",
    )
    summary["queued_retry"] = True
    return summary
