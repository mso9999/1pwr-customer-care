"""Derive commissioning payment steps from verified fee ledger rows."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger("cc-api.onboarding-derive")

PAYMENT_STEPS = (
    ("connection_fee", "connection_fee_paid"),
    ("readyboard_fee", "readyboard_fee_paid"),
)


def _as_date(value: datetime | date | None) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def derive_payment_steps_for_customer(
    conn,
    customer_id: int,
    *,
    account_number: str | None = None,
) -> list[str]:
    """Set connection/readyboard paid flags from verified payment_verifications."""
    cur = conn.cursor()
    if account_number:
        acct_filter = "pv.account_number = %s"
        params: list[object] = [account_number]
    else:
        cur.execute(
            "SELECT account_number FROM accounts WHERE customer_id = %s ORDER BY account_number LIMIT 1",
            (customer_id,),
        )
        row = cur.fetchone()
        if not row:
            return []
        account_number = row[0]
        acct_filter = "pv.account_number = %s"
        params = [account_number]

    updated: list[str] = []
    for payment_type, step in PAYMENT_STEPS:
        cur.execute(
            f"""
            SELECT pv.verified_at, t.transaction_date
            FROM payment_verifications pv
            LEFT JOIN transactions t ON t.id = pv.transaction_id
            WHERE {acct_filter}
              AND pv.payment_type = %s
              AND pv.status = 'verified'
            ORDER BY COALESCE(pv.verified_at, t.transaction_date) DESC NULLS LAST
            LIMIT 1
            """,
            params + [payment_type],
        )
        fee_row = cur.fetchone()
        if not fee_row:
            continue
        paid_date = _as_date(fee_row[0]) or _as_date(fee_row[1])
        date_col = f"{step}_date"
        cur.execute(
            f"""
            UPDATE customers
            SET {step} = TRUE,
                {date_col} = COALESCE(%s, {date_col}, CURRENT_DATE),
                updated_at = NOW()
            WHERE id = %s
              AND COALESCE({step}, FALSE) = FALSE
              AND COALESCE(payment_status_override, '') <> 'not_paid'
            """,
            (paid_date, customer_id),
        )
        if cur.rowcount:
            updated.append(step)
    return updated


def derive_payment_steps_for_accounts(conn, account_numbers: list[str]) -> int:
    if not account_numbers:
        return 0
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT a.customer_id, a.account_number
        FROM accounts a
        WHERE a.account_number = ANY(%s)
        """,
        (account_numbers,),
    )
    rows = cur.fetchall()
    total = 0
    for customer_id, account_number in rows:
        total += len(
            derive_payment_steps_for_customer(
                conn, int(customer_id), account_number=account_number
            )
        )
    return total
