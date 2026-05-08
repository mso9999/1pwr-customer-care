"""
Payment amount → category classifier.

A single source of truth used by every payment ingestion path
(``payments.py``: webhook + manual; ``ingest.py``: SMS) so a customer paying
exactly the country connection fee (501 LSL in Lesotho) gets booked as a
``connection_fee``, not as kWh credit. After the fee is paid (i.e. a
``verified`` row exists in ``payment_verifications``), the classifier reverts
to ``electricity`` for that account so future 501-LSL payments behave
normally.

Output is small and stable so callers can ``raise`` cleanly on errors:

    {
        "category": "connection_fee" | "readyboard_fee" | "electricity",
        "matched_amount": float | None,        # the configured fee that matched
        "currency": "LSL",
    }

This module is **read-only** -- it never writes ``transactions`` or
``payment_verifications`` rows. Callers do that and pass the chosen
``category`` to ``payment_verification.create_verification_entry`` when the
category is one of the fee types.

The amount-comparison uses an exact-cents test (rounding to 2 dp) so a
floating-point payment of 501.0 vs 501.00 vs 501 all match.
"""

from __future__ import annotations

import logging
from typing import Optional

from country_fees import get_country_fees

logger = logging.getLogger("cc-api.fee-classifier")


_AMOUNT_EPSILON = 0.005  # half a cent


def _amounts_match(paid: float, target: float) -> bool:
    """Exact-cents match (tolerant to FP rounding)."""
    if target <= 0:
        return False
    return abs(round(paid, 2) - round(target, 2)) < _AMOUNT_EPSILON


def _has_verified_fee(conn, account_number: str, payment_type: str) -> bool:
    """True if this account already has a verified row of this fee type."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM payment_verifications
            WHERE account_number = %s
              AND payment_type   = %s
              AND status         = 'verified'
            LIMIT 1
            """,
            (account_number, payment_type),
        )
        return cur.fetchone() is not None
    except Exception as exc:
        # If the table or column doesn't exist on this database (e.g. fresh
        # install before migration 019), treat as "not yet paid" so the
        # classifier still routes correctly the first time a 501 arrives.
        logger.warning("Fee verification lookup failed for %s: %s", account_number, exc)
        return False


def classify_payment(
    conn,
    account_number: str,
    amount: float,
    *,
    fees: Optional[dict] = None,
) -> dict:
    """Classify a payment by its amount.

    Args:
        conn: an open psycopg2 connection (used for DB lookups).
        account_number: account receiving the payment.
        amount: currency amount paid by the customer.
        fees: optional pre-fetched ``get_country_fees(conn)`` dict, to avoid
            a second round-trip when the caller already has it.

    Returns the classification dict described in the module docstring.
    """
    if amount is None or amount <= 0:
        return {
            "category": "electricity",
            "matched_amount": None,
            "currency": (fees or {}).get("currency", ""),
        }

    if fees is None:
        fees = get_country_fees(conn)

    conn_fee = float(fees.get("connection_fee_amount") or 0)
    rb_fee = float(fees.get("readyboard_fee_amount") or 0)
    currency = fees.get("currency", "")

    if _amounts_match(amount, conn_fee) and not _has_verified_fee(
        conn, account_number, "connection_fee"
    ):
        return {
            "category": "connection_fee",
            "matched_amount": conn_fee,
            "currency": currency,
        }

    if _amounts_match(amount, rb_fee) and not _has_verified_fee(
        conn, account_number, "readyboard_fee"
    ):
        return {
            "category": "readyboard_fee",
            "matched_amount": rb_fee,
            "currency": currency,
        }

    return {
        "category": "electricity",
        "matched_amount": None,
        "currency": currency,
    }
