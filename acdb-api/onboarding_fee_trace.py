"""Persist fee workbook vs ledger trace categories on customers (1PDB)."""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger("cc-api.onboarding-fee-trace")

FEE_TRACE_CATEGORIES = frozenset(
    {
        "listed_paid_missing_record",
        "resolved_reference_linked",
        "waived_not_required",
        "investigating",
    }
)


def clear_listed_missing_if_fee_verified(conn, account_numbers: Iterable[str]) -> None:
    """After a fee is verified, clear listed_paid_missing_record for that fee type on the customer."""
    accounts = sorted({a.strip().upper() for a in account_numbers if a and str(a).strip()})
    if not accounts:
        return
    cur = conn.cursor()
    for payment_type, col in (
        ("connection_fee", "connection_fee_trace_category"),
        ("readyboard_fee", "readyboard_fee_trace_category"),
    ):
        cur.execute(
            f"""
            UPDATE customers c
            SET {col} = NULL,
                fee_trace_updated_at = NOW()
            WHERE c.{col} = 'listed_paid_missing_record'
              AND c.id IN (SELECT customer_id FROM accounts WHERE account_number = ANY(%s))
              AND EXISTS (
                SELECT 1
                FROM payment_verifications pv
                JOIN accounts a ON a.account_number = pv.account_number AND a.customer_id = c.id
                WHERE pv.payment_type = %s
                  AND pv.status = 'verified'
              )
            """,
            (accounts, payment_type),
        )
        if cur.rowcount:
            logger.info("Cleared %s for %d customers after verify", col, cur.rowcount)
