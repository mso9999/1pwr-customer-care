"""
Unmetered service status + monthly flat service-fee ledger.

Business rule (LS, 2026-08): a customer whose service is CONNECTED but who has
no meter yet is not on prepaid kWh — they owe a flat monthly service fee
(default 50 LSL; ``system_config`` key ``unmetered_service_fee_amount``,
editable via /api/admin/country-fees; seed default lives on
``CountryConfig.default_unmetered_service_fee``).

Model (mirrors ``account_advances`` / ``account_advance_ledger``, migration 019;
tables in migration 068):

- ``unmetered_service`` — one ACTIVE row per account (the enrollment).
  ``outstanding`` is the current unpaid service-fee debt; ``monthly_fee`` is a
  snapshot taken at enrollment so later fee edits don't rewrite history.
- ``unmetered_service_ledger`` — append-only audit trail (``accrual`` /
  ``repayment`` / ``adjustment``). Monthly accrual is idempotent via a partial
  unique index on ``(service_id, accrual_period)``.

Entry is a MANUAL staff action (POST /api/unmetered-service). Exit is
AUTOMATIC: meter assignment (meter_lifecycle), commissioning
(commission/execute), or the accrual job's active-meter guard end the
enrollment; staff can also end it manually.

Payment integration: ``fee_debt.compute_fee_then_advance_split`` carves a
``service_fee_portion`` out of an electricity top-up (after onboarding fee
debt, before the advance split) when the account has an active enrollment, and
the caller applies it with ``apply_service_fee_payment`` — same pattern as
``apply_advance_payment``. The split is recorded on the transaction row
(``service_fee_portion`` / ``unmetered_service_id``).

Monthly accrual: ``accrue_period`` is called by
``scripts/ops/accrue_unmetered_service_fees.py`` (systemd timer
``cc-unmetered-accrual.timer``, 1st of the month). An enrollment active on the
1st accrues that month's fee; mid-month enrollments are first charged on the
next 1st. The accrual also auto-ends enrollments whose account now has an
active meter (belt-and-braces behind the assignment/commissioning hooks).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from country_config import COUNTRY
from middleware import require_employee
from models import CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.unmetered-service")

router = APIRouter(prefix="/api/unmetered-service", tags=["unmetered-service"])

END_REASONS = {"meter_assigned", "commissioned", "terminated", "manual"}

_ACTIVE_METER_PREDICATE = """
    SELECT 1 FROM meters m
    WHERE m.account_number = %s
      AND LOWER(COALESCE(m.status::text, 'active')) NOT IN ('decommissioned', 'retired')
    LIMIT 1
"""


# ---------------------------------------------------------------------------
# Payment-split helpers (called from fee_debt / payments.py / ingest.py)
# ---------------------------------------------------------------------------


def get_active_enrollment(conn, account_number: str) -> Optional[dict]:
    """Return the account's active unmetered-service enrollment, or None.

    Degrades gracefully when migration 068 has not been applied yet (deploy
    window: APIs restart before migrations run) — callers fall back to a full
    electricity credit, exactly like ``advances.get_active_advance``.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, account_number, customer_id, monthly_fee,
                   repayment_fraction, outstanding, currency, started_at
            FROM unmetered_service
            WHERE account_number = %s AND status = 'active'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (account_number,),
        )
    except Exception as exc:
        err = str(exc).lower()
        if "unmetered_service" in err or "does not exist" in err:
            conn.rollback()
            logger.debug("unmetered_service table missing — no service-fee split: %s", exc)
            return None
        raise
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "account_number": row[1],
        "customer_id": row[2],
        "monthly_fee": float(row[3]),
        "repayment_fraction": float(row[4]),
        "outstanding": float(row[5]),
        "currency": row[6],
        "started_at": row[7].isoformat() if row[7] else None,
    }


def compute_service_fee_split(enrollment: Optional[dict], amount: float) -> dict:
    """Carve the service-fee repayment out of an electricity top-up remainder.

    The service-fee portion is ``repayment_fraction`` of ``amount``, capped at
    the outstanding debt so we never over-collect; the remainder flows on to
    the advance split / electricity credit. Mirrors
    ``advances.compute_advance_split``.
    """
    if not enrollment:
        return {
            "service_fee_portion": 0.0,
            "remainder_portion": round(amount, 2),
            "unmetered_service_id": None,
        }
    fraction = float(enrollment.get("repayment_fraction") or 0)
    outstanding = float(enrollment.get("outstanding") or 0)
    raw = round(amount * fraction, 2)
    portion = round(min(raw, outstanding), 2)
    return {
        "service_fee_portion": portion,
        "remainder_portion": round(amount - portion, 2),
        "unmetered_service_id": int(enrollment["id"]),
    }


def apply_service_fee_payment(
    conn,
    service_id: int,
    amount: float,
    source_transaction_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> tuple[float, float]:
    """Decrement an enrollment's outstanding debt and write a ledger row.

    Returns ``(new_outstanding, amount_applied)``. Does NOT commit — the caller
    (payments.py / ingest.py) commits after the full transaction body succeeds.
    The enrollment stays active when outstanding hits zero: the customer is
    still unmetered and keeps accruing monthly fees until the enrollment ends.
    """
    if amount <= 0:
        return (0.0, 0.0)

    cur = conn.cursor()
    cur.execute(
        "SELECT outstanding FROM unmetered_service WHERE id = %s AND status = 'active' FOR UPDATE",
        (service_id,),
    )
    row = cur.fetchone()
    if not row:
        return (0.0, 0.0)

    outstanding = float(row[0])
    repay = round(min(amount, outstanding), 2)
    if repay <= 0:
        return (outstanding, 0.0)
    new_outstanding = round(outstanding - repay, 2)

    cur.execute(
        """
        INSERT INTO unmetered_service_ledger
            (service_id, entry_type, amount, balance_after,
             source_transaction_id, created_by, note)
        VALUES (%s, 'repayment', %s, %s, %s, %s, %s)
        """,
        (
            service_id, repay, new_outstanding,
            source_transaction_id, created_by,
            f"Service-fee repayment of {repay:.2f} from txn {source_transaction_id or '-'}",
        ),
    )
    cur.execute(
        "UPDATE unmetered_service SET outstanding = %s, updated_at = NOW() WHERE id = %s",
        (new_outstanding, service_id),
    )
    return (new_outstanding, repay)


# ---------------------------------------------------------------------------
# Exit helper (meter assignment / commissioning hooks + manual endpoint)
# ---------------------------------------------------------------------------


def end_unmetered_service(
    conn,
    account_number: str,
    reason: str,
    ended_by: Optional[str] = None,
) -> bool:
    """End the account's active enrollment, if any. Returns True when one ended.

    Never raises on a missing table (deploy window) — callers treat the hook as
    best-effort and log. Does NOT commit; the caller commits.
    """
    if reason not in END_REASONS:
        reason = "manual"
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE unmetered_service
               SET status = 'ended', ended_at = NOW(), end_reason = %s, updated_at = NOW()
             WHERE account_number = %s AND status = 'active'
             RETURNING id
            """,
            (reason, account_number),
        )
    except Exception as exc:
        err = str(exc).lower()
        if "unmetered_service" in err or "does not exist" in err:
            conn.rollback()
            logger.debug("unmetered_service table missing — exit hook no-op: %s", exc)
            return False
        raise
    row = cur.fetchone()
    if not row:
        return False
    service_id = int(row[0])
    cur.execute(
        """
        SELECT outstanding FROM unmetered_service WHERE id = %s
        """,
        (service_id,),
    )
    outstanding = float(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO unmetered_service_ledger
            (service_id, entry_type, amount, balance_after, created_by, note)
        VALUES (%s, 'adjustment', 0, %s, %s, %s)
        """,
        (
            service_id, outstanding, ended_by,
            f"Enrollment ended ({reason})"
            + (f" with {outstanding:.2f} outstanding" if outstanding > 0 else ""),
        ),
    )
    logger.info(
        "unmetered-service ended: id=%d acct=%s reason=%s by=%s",
        service_id, account_number, reason, ended_by,
    )
    return True


# ---------------------------------------------------------------------------
# Monthly accrual (called by scripts/ops/accrue_unmetered_service_fees.py)
# ---------------------------------------------------------------------------


def accrue_period(conn, period: str, *, dry_run: bool = False) -> dict:
    """Accrue one month's service fee onto every active enrollment.

    Idempotent per ``period`` (YYYY-MM) via the partial unique index on
    ``(service_id, accrual_period)`` for ``entry_type = 'accrual'``. Enrollments
    whose account now has an active meter are auto-ended (reason
    ``meter_assigned``) instead of accruing — the exit hook safety net.
    Does NOT commit; the caller commits (or rolls back for dry_run).
    """
    cur = conn.cursor()
    summary = {
        "period": period,
        "enrollments_considered": 0,
        "accruals_applied": 0,
        "accruals_skipped_duplicate": 0,
        "auto_ended_metered": 0,
        "total_fee_amount": 0.0,
    }

    cur.execute(
        """
        SELECT id, account_number, monthly_fee, outstanding, currency
        FROM unmetered_service
        WHERE status = 'active'
        ORDER BY id ASC
        FOR UPDATE
        """
    )
    rows = cur.fetchall()
    summary["enrollments_considered"] = len(rows)

    for service_id, account, monthly_fee, outstanding, currency in rows:
        monthly_fee = float(monthly_fee)
        outstanding = float(outstanding)

        # Auto-exit guard: an active meter means the customer is no longer
        # unmetered — end the enrollment rather than charging another month.
        cur.execute(_ACTIVE_METER_PREDICATE, (account,))
        if cur.fetchone():
            if not dry_run:
                end_unmetered_service(conn, account, "meter_assigned", "system:accrual")
            summary["auto_ended_metered"] += 1
            logger.info(
                "[accrual] auto-ended service=%d acct=%s (active meter found)", service_id, account,
            )
            continue

        cur.execute(
            """
            SELECT 1 FROM unmetered_service_ledger
            WHERE service_id = %s AND entry_type = 'accrual' AND accrual_period = %s
            LIMIT 1
            """,
            (service_id, period),
        )
        if cur.fetchone():
            summary["accruals_skipped_duplicate"] += 1
            continue

        new_outstanding = round(outstanding + monthly_fee, 2)
        if dry_run:
            logger.info(
                "[dry-run] service=%d acct=%s outstanding=%.2f → +%.2f = %.2f %s",
                service_id, account, outstanding, monthly_fee, new_outstanding, currency,
            )
        else:
            cur.execute(
                """
                INSERT INTO unmetered_service_ledger
                    (service_id, entry_type, amount, balance_after,
                     accrual_period, created_by, note)
                VALUES (%s, 'accrual', %s, %s, %s, 'system:accrual', %s)
                """,
                (
                    service_id, monthly_fee, new_outstanding, period,
                    f"Monthly unmetered service fee {monthly_fee:.2f} {currency} ({period})",
                ),
            )
            cur.execute(
                "UPDATE unmetered_service SET outstanding = %s, updated_at = NOW() WHERE id = %s",
                (new_outstanding, service_id),
            )
        summary["accruals_applied"] += 1
        summary["total_fee_amount"] += monthly_fee

    return summary


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class EnrollRequest(BaseModel):
    account_number: str = Field(..., min_length=3, max_length=32)
    monthly_fee: Optional[float] = Field(
        None, gt=0,
        description="Override the country-configured monthly service fee (snapshot).",
    )
    repayment_fraction: Optional[float] = Field(
        None, ge=0, lt=1,
        description="Fraction of each top-up remainder that pays down the service-fee debt (default 0.5).",
    )
    opening_outstanding: Optional[float] = Field(
        None, ge=0,
        description="Pre-existing arrears to record at enrollment (e.g. months already owed).",
    )
    note: Optional[str] = Field(None, max_length=500)


class EndRequest(BaseModel):
    reason: Optional[str] = Field(None, description="manual | terminated (meter exits are automatic)")
    note: Optional[str] = Field(None, max_length=500)


def _enrollment_dict(row, cols) -> dict:
    d = dict(zip(cols, row))
    for k in ("started_at", "ended_at", "created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    for k in ("monthly_fee", "outstanding", "repayment_fraction"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d


@router.get("")
def list_enrollments(
    status: str = Query("active", description="active | ended | all"),
    site: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_employee),
):
    """List unmetered-service enrollments with customer/site context."""
    status_filter = ""
    params: list[Any] = []
    if status == "active":
        status_filter = "WHERE us.status = 'active'"
    elif status == "ended":
        status_filter = "WHERE us.status = 'ended'"
    elif status != "all":
        raise HTTPException(status_code=400, detail="status must be active | ended | all")

    site_clause = ""
    if site:
        site_clause = ("AND" if status_filter else "WHERE") + " c.community = %s"
        params.append(site.strip().upper())

    from customer_api import get_connection  # lazy: avoid import cycle

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT us.id, us.account_number, us.customer_id, us.status,
                   us.monthly_fee, us.outstanding, us.currency,
                   us.repayment_fraction, us.started_at, us.ended_at, us.end_reason,
                   us.note, us.created_by, us.created_at,
                   c.first_name, c.last_name, c.community,
                   (SELECT COUNT(*) FROM unmetered_service_ledger l
                     WHERE l.service_id = us.id AND l.entry_type = 'accrual') AS months_accrued,
                   (SELECT MAX(l.accrual_period) FROM unmetered_service_ledger l
                     WHERE l.service_id = us.id AND l.entry_type = 'accrual') AS last_accrual_period
            FROM unmetered_service us
            LEFT JOIN customers c ON c.id = us.customer_id
            {status_filter}
            {site_clause}
            ORDER BY us.status = 'ended', us.started_at DESC
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        rows = [_enrollment_dict(r, cols) for r in cur.fetchall()]

    active = [r for r in rows if r["status"] == "active"]
    return {
        "count": len(rows),
        "active_count": len(active),
        "total_outstanding": round(sum(r["outstanding"] for r in active), 2),
        "currency": COUNTRY.currency,
        "currency_symbol": COUNTRY.currency_symbol,
        "monthly_fee_configured": _configured_fee(),
        "enrollments": rows,
    }


def _configured_fee() -> float:
    from country_fees import get_country_fees  # lazy: avoid import cycle
    from customer_api import get_connection

    with get_connection() as conn:
        return float(get_country_fees(conn).get("unmetered_service_fee_amount") or 0.0)


@router.get("/by-account/{account_number}")
def get_by_account(account_number: str, user: CurrentUser = Depends(require_employee)):
    """Current unmetered-service status for one account (customer detail badge)."""
    from customer_api import get_connection  # lazy: avoid import cycle

    with get_connection() as conn:
        enrollment = get_active_enrollment(conn, account_number.strip())
    return {
        "account_number": account_number.strip(),
        "enrolled": enrollment is not None,
        "enrollment": enrollment,
        "currency_symbol": COUNTRY.currency_symbol,
    }


@router.get("/{service_id}")
def get_enrollment(service_id: int, user: CurrentUser = Depends(require_employee)):
    """Enrollment detail + full ledger."""
    from customer_api import get_connection  # lazy: avoid import cycle

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT us.id, us.account_number, us.customer_id, us.status,
                   us.monthly_fee, us.outstanding, us.currency,
                   us.repayment_fraction, us.started_at, us.ended_at, us.end_reason,
                   us.note, us.created_by, us.created_at,
                   c.first_name, c.last_name, c.community
            FROM unmetered_service us
            LEFT JOIN customers c ON c.id = us.customer_id
            WHERE us.id = %s
            """,
            (service_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Enrollment not found")
        cols = [d[0] for d in cur.description]
        enrollment = _enrollment_dict(row, cols)

        cur.execute(
            """
            SELECT id, entry_type, amount, balance_after, accrual_period,
                   source_transaction_id, created_by, note, created_at
            FROM unmetered_service_ledger
            WHERE service_id = %s
            ORDER BY id ASC
            """,
            (service_id,),
        )
        lcols = [d[0] for d in cur.description]
        ledger = []
        for r in cur.fetchall():
            d = dict(zip(lcols, r))
            d["amount"] = float(d["amount"])
            d["balance_after"] = float(d["balance_after"])
            if d.get("created_at") is not None:
                d["created_at"] = d["created_at"].isoformat()
            ledger.append(d)

    return {"enrollment": enrollment, "ledger": ledger}


@router.post("")
def enroll(req: EnrollRequest, user: CurrentUser = Depends(require_employee)):
    """Enroll a connected-but-unmetered account in monthly service-fee billing.

    Manual entry (ops decision). Blocked when the account already has an active
    meter (that contradicts 'unmetered') or is already enrolled.
    """
    from country_fees import _require_fee_admin, get_country_fees  # lazy: avoid import cycle
    from customer_api import get_connection

    _require_fee_admin(user)
    account = req.account_number.strip().upper()

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT a.account_number, c.id, c.first_name, c.last_name, c.community,
                   c.date_service_connected
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE UPPER(a.account_number) = %s
            LIMIT 1
            """,
            (account,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Account '{account}' not found")
        (_acct, customer_id, first_name, last_name, community, date_connected) = row

        cur.execute(_ACTIVE_METER_PREDICATE, (account,))
        if cur.fetchone():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Account {account} already has an active meter — "
                    "unmetered service does not apply."
                ),
            )

        fee = (
            float(req.monthly_fee)
            if req.monthly_fee is not None
            else float(get_country_fees(conn).get("unmetered_service_fee_amount") or 0.0)
        )
        if fee <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No unmetered service fee configured for this country — "
                    "set unmetered_service_fee_amount under Tariff Management → Country fees."
                ),
            )

        opening = round(float(req.opening_outstanding or 0), 2)
        fraction = (
            float(req.repayment_fraction)
            if req.repayment_fraction is not None
            else 0.5
        )

        try:
            cur.execute(
                """
                INSERT INTO unmetered_service
                    (account_number, customer_id, monthly_fee, repayment_fraction,
                     outstanding, currency, note, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    account, customer_id, fee, fraction, opening,
                    COUNTRY.currency, (req.note or "").strip() or None, user.user_id,
                ),
            )
        except Exception as exc:
            conn.rollback()
            if "uq_unmetered_service_active" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail=f"Account {account} is already enrolled in unmetered service.",
                )
            raise
        service_id = int(cur.fetchone()[0])

        if opening > 0:
            cur.execute(
                """
                INSERT INTO unmetered_service_ledger
                    (service_id, entry_type, amount, balance_after, created_by, note)
                VALUES (%s, 'adjustment', %s, %s, %s, %s)
                """,
                (
                    service_id, opening, opening, user.user_id,
                    "Opening arrears recorded at enrollment",
                ),
            )

        try_log_mutation(
            user, "insert", "unmetered_service", str(service_id),
            new_values={
                "account_number": account,
                "customer_id": customer_id,
                "monthly_fee": fee,
                "repayment_fraction": fraction,
                "opening_outstanding": opening,
                "date_service_connected": str(date_connected) if date_connected else None,
            },
            metadata={"kind": "unmetered_service_enroll", "endpoint": "POST /api/unmetered-service"},
            conn=conn,
        )
        conn.commit()

    logger.info(
        "unmetered-service enrolled: id=%d acct=%s fee=%.2f %s by=%s",
        service_id, account, fee, COUNTRY.currency, user.user_id,
    )
    return {
        "status": "ok",
        "id": service_id,
        "account_number": account,
        "customer_name": f"{first_name or ''} {last_name or ''}".strip(),
        "site": community,
        "monthly_fee": fee,
        "repayment_fraction": fraction,
        "outstanding": opening,
        "currency": COUNTRY.currency,
        "currency_symbol": COUNTRY.currency_symbol,
        "date_service_connected": str(date_connected) if date_connected else None,
    }


@router.post("/{service_id}/end")
def end_enrollment(service_id: int, req: EndRequest, user: CurrentUser = Depends(require_employee)):
    """Manually end an enrollment (meter exits are automatic)."""
    from country_fees import _require_fee_admin  # lazy: avoid import cycle
    from customer_api import get_connection

    _require_fee_admin(user)
    reason = (req.reason or "manual").strip()
    if reason in ("meter_assigned", "commissioned"):
        reason = "manual"

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT account_number, status FROM unmetered_service WHERE id = %s",
            (service_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Enrollment not found")
        account, status = row
        if status != "active":
            raise HTTPException(status_code=409, detail="Enrollment is already ended")

        ended = end_unmetered_service(conn, account, reason, user.user_id)
        if not ended:
            raise HTTPException(status_code=409, detail="Enrollment is already ended")

        try_log_mutation(
            user, "update", "unmetered_service", str(service_id),
            new_values={"status": "ended", "end_reason": reason, "note": req.note},
            metadata={"kind": "unmetered_service_end", "endpoint": "POST /api/unmetered-service/{id}/end"},
            conn=conn,
        )
        conn.commit()

    return {"status": "ok", "id": service_id, "account_number": account, "end_reason": reason}
