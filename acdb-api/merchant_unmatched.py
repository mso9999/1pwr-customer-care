"""
Holding queue for merchant-line payments that matched no CC account.

Merchant (M-Pesa "sent to merchant") payments only enter CC via the merchant-export
backfill; historically, rows whose Reference resolved to no account were silently
dropped (RCA 2026-06-11, 0231MAK). Instead, the backfill now parks them in
``merchant_unmatched_payments`` and account registration claims them automatically.

Claiming books through the same paths as the manual-payment endpoint:
fee amounts -> ``record_fee_transaction`` + verification + fee-debt reduction;
anything else -> ``record_historical_payment_transaction`` (ledger-only, NO kWh
credit — historical merchant payments must not move re-anchored balances).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("cc-api.merchant-unmatched")

_ACCOUNT_RE = re.compile(r"\b(\d{4}[A-Z]{2,4})\b")

# Internal treasury movements present in merchant exports (org-level money handling,
# NOT customer payments). Ring-fenced: parked as category='treasury', never claimable.
_TREASURY_RE = re.compile(
    r"transfer of funds from m-?pesa"
    r"|control account"
    r"|organi[sz]ation deposit"
    r"|deposit of funds",
    re.IGNORECASE,
)


def is_treasury_transfer(reference_text: str) -> bool:
    """True if a merchant-export row is an internal org transfer, not a customer payment."""
    return bool(_TREASURY_RE.search(reference_text or ""))


def park_unmatched_payment(
    conn,
    *,
    receipt: str,
    amount: float,
    paid_at: datetime,
    reference_text: str = "",
    payer_phone: str = "",
    site_hint: str = "",
    provider: str = "",
    source_file: str = "",
) -> bool:
    """Insert (or refresh) an unmatched merchant payment. Returns True if newly parked."""
    receipt = (receipt or "").strip()
    if not receipt:
        return False
    category = "treasury" if is_treasury_transfer(reference_text) else "customer"
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO merchant_unmatched_payments
            (receipt, amount, paid_at, reference_text, payer_phone,
             site_hint, provider, source_file, category)
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM merchant_unmatched_payments WHERE lower(receipt) = lower(%s)
        )
        """,
        (
            receipt, round(float(amount), 2), paid_at,
            (reference_text or "")[:500], payer_phone or "",
            site_hint or "", provider or "", (source_file or "")[:200],
            category,
            receipt,
        ),
    )
    return cur.rowcount > 0


def claim_unmatched_for_account(conn, account_number: str) -> list[dict[str, Any]]:
    """Book any parked payments whose reference cites *account_number*.

    Called after account creation (registration). Best-effort: failures are logged,
    never raised into the registration path. Returns a list of booked-payment dicts.
    """
    from balance_engine import record_fee_transaction, record_historical_payment_transaction
    from fee_classifier import classify_payment
    from fee_debt import apply_fee_payment_category_to_debt
    from payment_verification import create_verification_entry
    from payments import _get_tariff_rate, _resolve_meter

    account = (account_number or "").strip().upper()
    if not account:
        return []

    booked: list[dict[str, Any]] = []
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, receipt, amount, paid_at, reference_text
            FROM merchant_unmatched_payments
            WHERE resolved_at IS NULL
              AND category = 'customer'   -- treasury rows are ring-fenced, never claimable
            ORDER BY paid_at
            """
        )
        candidates = [
            row for row in cur.fetchall()
            if account in {m.upper() for m in _ACCOUNT_RE.findall((row[4] or "").upper())}
        ]
    except Exception as exc:
        logger.warning("Unmatched-payment lookup failed for %s: %s", account, exc)
        return []

    for row_id, receipt, amount, paid_at, _ref in candidates:
        try:
            cur.execute(
                """
                SELECT 1 FROM transactions
                WHERE lower(trim(payment_reference)) = lower(trim(%s)) LIMIT 1
                """,
                (receipt,),
            )
            if cur.fetchone():
                cur.execute(
                    """
                    UPDATE merchant_unmatched_payments
                    SET resolved_at = NOW(), resolved_account = %s
                    WHERE id = %s
                    """,
                    (account, row_id),
                )
                continue

            amt = float(amount)
            meter_id = _resolve_meter(conn, account)
            cls = classify_payment(conn, account, amt)
            category = cls["category"]
            if category in ("connection_fee", "readyboard_fee"):
                txn_id, _ = record_fee_transaction(
                    conn, account, meter_id, amt, category,
                    source="portal", timestamp=paid_at, payment_reference=receipt,
                )
                create_verification_entry(conn, txn_id, account, category, amt)
                apply_fee_payment_category_to_debt(conn, account, category, amt)
            else:
                rate = _get_tariff_rate(conn, account)
                txn_id, _ = record_historical_payment_transaction(
                    conn, account, meter_id, amt, rate,
                    source="portal", timestamp=paid_at, payment_reference=receipt,
                )

            cur.execute(
                """
                UPDATE merchant_unmatched_payments
                SET resolved_at = NOW(), resolved_txn_id = %s, resolved_account = %s
                WHERE id = %s
                """,
                (txn_id, account, row_id),
            )
            booked.append({
                "receipt": receipt,
                "amount": amt,
                "paid_at": paid_at.isoformat() if paid_at else None,
                "category": category,
                "transaction_id": txn_id,
            })
            logger.info(
                "Claimed parked merchant payment %s (%.2f, %s) for %s -> txn %s",
                receipt, amt, category, account, txn_id,
            )
        except Exception as exc:
            logger.warning("Claim of parked payment %s for %s failed: %s", receipt, account, exc)
    return booked
