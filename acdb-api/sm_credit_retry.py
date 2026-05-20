"""Durable retry queue for CC -> SparkMeter credit pushes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from sparkmeter_credit import credit_sparkmeter

logger = logging.getLogger("cc-api.sm-credit-retry")

MAX_RETRY_BACKOFF_SECONDS = 3600
FINAL_FAIL_ATTEMPTS = 20


def _next_retry_seconds(attempt_count: int) -> int:
    # 1m, 2m, 4m, ... capped at 60m
    if attempt_count < 0:
        attempt_count = 0
    return min(60 * (2 ** attempt_count), MAX_RETRY_BACKOFF_SECONDS)


def enqueue_sm_credit_retry(
    *,
    account_number: str,
    amount: float,
    memo: str,
    external_id: str | None,
    error: str | None = None,
) -> None:
    """Insert or refresh a pending credit retry row."""
    from customer_api import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sm_credit_retry_queue
                (account_number, amount, memo, external_id, status,
                 first_error, last_error, next_retry_at)
            VALUES (%s, %s, %s, NULLIF(%s, ''), 'pending', %s, %s, NOW())
            ON CONFLICT (external_id) DO UPDATE
            SET account_number = EXCLUDED.account_number,
                amount = EXCLUDED.amount,
                memo = EXCLUDED.memo,
                status = 'pending',
                last_error = EXCLUDED.last_error,
                next_retry_at = NOW(),
                resolved_at = NULL
            """,
            (
                account_number,
                float(amount),
                memo or "",
                (external_id or "").strip(),
                error,
                error,
            ),
        )
        conn.commit()


def process_due_sm_credit_retries(limit: int = 20) -> Dict[str, int]:
    """Retry due rows from ``sm_credit_retry_queue`` and return counters."""
    from customer_api import get_connection

    processed = 0
    ok = 0
    failed = 0

    with get_connection() as conn:
        cur = conn.cursor()
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

            result = credit_sparkmeter(account, amount, memo, external_id)
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

    return {"processed": processed, "ok": ok, "failed": failed}


def credit_sm_with_retry(
    *,
    account_number: str,
    amount: float,
    memo: str,
    external_id: str | None,
    replay_due_limit: int = 3,
) -> Dict[str, Any]:
    """Try immediate credit; on failure queue for durable retry."""
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
    )
    summary["queued_retry"] = True
    return summary
