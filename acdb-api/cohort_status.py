"""
Manual cohort funnel status overrides (Customer Cohort + customer dashboard).

Distinct from ``payment_status_override`` (simple not_paid/paid/fully_paid for
analytics).  Cohort overrides use the full ``COHORT_STATUSES`` vocabulary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from customer_api import get_connection
from customer_cohort import COHORT_STATUSES, _column_exists, _resolve_fee_threshold
from country_config import _REGISTRY
from middleware import require_employee
from models import CurrentUser

logger = logging.getLogger("cc-api.cohort_status")

router = APIRouter(prefix="/api/cohort-status", tags=["cohort-status"])


class SetCohortOverrideRequest(BaseModel):
    status: str
    note: Optional[str] = None


def _compute_inferred_cohort_status(
    *,
    total_paid: float,
    fee_threshold: float,
    payment_override: Optional[str],
    date_connected,
    date_terminated,
    not_metered: bool,
) -> str:
    if date_terminated is not None:
        return "terminated"
    if total_paid <= 0 or payment_override == "not_paid":
        return "not_paid"
    fully = payment_override == "fully_paid" or total_paid >= fee_threshold
    partial = not fully
    if fully and date_connected is not None:
        return "fully_paid_not_metered" if not_metered else "fully_paid_connected"
    if fully:
        return "fully_paid_not_connected"
    if date_connected is not None:
        return "partially_paid_not_metered" if not_metered else "partially_paid_connected"
    return "partially_paid_not_connected"


@router.get("/{customer_id}/inferred")
def get_inferred_cohort_status(
    customer_id: int,
    user: CurrentUser = Depends(require_employee),
):
    """Computed cohort status vs manual override for one customer."""
    with get_connection() as conn:
        cur = conn.cursor()
        meter_exists_expr = """EXISTS (
            SELECT 1 FROM meters m
            INNER JOIN accounts a_m ON a_m.account_number = m.account_number
            WHERE a_m.customer_id = c.id
              AND LOWER(COALESCE(m.status, 'active')) NOT IN ('decommissioned', 'retired')
        )"""
        if _column_exists(cur, "customers", "meter_installed"):
            meter_expr = f"(COALESCE(c.meter_installed, false) OR {meter_exists_expr})"
        else:
            meter_expr = meter_exists_expr
        has_fee_debt_cols = (
            _column_exists(cur, "customers", "fee_debt_connection_remaining")
            and _column_exists(cur, "customers", "fee_debt_readyboard_remaining")
        )
        fee_debt_conn_expr = (
            "c.fee_debt_connection_remaining"
            if has_fee_debt_cols
            else "NULL::numeric AS fee_debt_connection_remaining"
        )
        fee_debt_rb_expr = (
            "c.fee_debt_readyboard_remaining"
            if has_fee_debt_cols
            else "NULL::numeric AS fee_debt_readyboard_remaining"
        )
        if _column_exists(cur, "customers", "cohort_status_override"):
            override_sel = "c.cohort_status_override"
            override_meta = "c.cohort_status_override_by, c.cohort_status_override_at"
        else:
            override_sel = "NULL::text AS cohort_status_override"
            override_meta = "NULL::text AS cohort_status_override_by, NULL::timestamptz AS cohort_status_override_at"
        cur.execute(
            f"""
            SELECT c.community,
                   c.date_service_connected,
                   c.date_service_terminated,
                   c.payment_status_override,
                   {override_sel},
                   {override_meta},
                   {fee_debt_conn_expr},
                   {fee_debt_rb_expr},
                   {meter_expr} AS meter_installed,
                   a.account_number
            FROM customers c
            LEFT JOIN accounts a ON a.customer_id = c.id
            WHERE c.id = %s
            LIMIT 1
            """,
            (customer_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Customer {customer_id} not found")

        community = row[0] or ""
        country = ""
        for code, cfg in _REGISTRY.items():
            if community.upper() in cfg.site_abbrev:
                country = code
                break
        fee_threshold = _resolve_fee_threshold(country or None)
        account_number = row[10]

        total_paid = 0.0
        if account_number:
            cur.execute(
                """
                SELECT COALESCE(SUM(t.transaction_amount), 0)
                FROM transactions t
                WHERE t.account_number = %s
                  AND t.is_payment = true
                  AND t.transaction_amount > 0
                """,
                (account_number,),
            )
            total_paid = float(cur.fetchone()[0] or 0)

        meter_installed = bool(row[9])
        not_metered = row[1] is not None and not meter_installed
        fee_debt_conn = float(row[7] or 0)
        fee_debt_rb = float(row[8] or 0)
        has_fee_debt = row[7] is not None or row[8] is not None
        fully_paid_from_debt = has_fee_debt and (fee_debt_conn + fee_debt_rb) <= 0.005
        payment_override = row[3]
        if payment_override == "fully_paid":
            inferred = "fully_paid_not_metered" if not_metered else (
                "fully_paid_connected" if row[1] is not None else "fully_paid_not_connected"
            )
        elif fully_paid_from_debt and row[2] is None:
            inferred = "fully_paid_not_metered" if not_metered else (
                "fully_paid_connected" if row[1] is not None else "fully_paid_not_connected"
            )
        else:
            inferred = _compute_inferred_cohort_status(
                total_paid=total_paid,
                fee_threshold=fee_threshold,
                payment_override=payment_override,
                date_connected=row[1],
                date_terminated=row[2],
                not_metered=not_metered,
            )
        override = row[4]
        effective = override if override else inferred

    return {
        "inferred_status": inferred,
        "effective_status": effective,
        "cohort_status_override": override,
        "cohort_status_override_by": row[5],
        "cohort_status_override_at": row[6].isoformat() if row[6] else None,
        "total_paid": round(total_paid, 2),
        "fee_threshold": round(fee_threshold, 2),
        "meter_installed": meter_installed,
        "has_override": override is not None,
        "allowed_statuses": COHORT_STATUSES,
    }


@router.post("/{customer_id}/override")
def set_cohort_status_override(
    customer_id: int,
    body: SetCohortOverrideRequest,
    user: CurrentUser = Depends(require_employee),
):
    status = body.status.strip()
    if status not in COHORT_STATUSES:
        raise HTTPException(400, f"status must be one of {COHORT_STATUSES}")

    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        cur = conn.cursor()
        if not _column_exists(cur, "customers", "cohort_status_override"):
            raise HTTPException(
                503,
                "cohort_status_override not available — apply migration 032_cohort_status_override.sql",
            )
        cur.execute(
            """
            UPDATE customers
               SET cohort_status_override = %s,
                   cohort_status_override_by = %s,
                   cohort_status_override_at = %s
             WHERE id = %s
            """,
            (status, user.user_id, now, customer_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Customer {customer_id} not found")

    logger.info(
        "cohort_status_override set: customer=%d status=%s by=%s",
        customer_id, status, user.user_id,
    )
    return {
        "cohort_status_override": status,
        "cohort_status_override_by": user.user_id,
        "cohort_status_override_at": now.isoformat(),
    }


@router.delete("/{customer_id}/override")
def clear_cohort_status_override(
    customer_id: int,
    user: CurrentUser = Depends(require_employee),
):
    with get_connection() as conn:
        cur = conn.cursor()
        if not _column_exists(cur, "customers", "cohort_status_override"):
            raise HTTPException(
                503,
                "cohort_status_override not available — apply migration 032_cohort_status_override.sql",
            )
        cur.execute(
            """
            UPDATE customers
               SET cohort_status_override = NULL,
                   cohort_status_override_by = NULL,
                   cohort_status_override_at = NULL
             WHERE id = %s
            """,
            (customer_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"Customer {customer_id} not found")

    logger.info("cohort_status_override cleared: customer=%d by=%s", customer_id, user.user_id)
    return {"cohort_status_override": None}
