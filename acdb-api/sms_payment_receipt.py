"""Post-success SMS with payment acknowledgement and 1PDB balance.

One SMS per payment event: **account holder phone first** (cell_phone_1 → phone
→ cell_phone_2), else **payer handset** (M-Pesa / gateway sender). Avoids
duplicate CM.com sends when payer and holder differed (previous behaviour sent
to both).
"""

from __future__ import annotations

import logging
import os

from balance_engine import get_balance_kwh
from country_config import COUNTRY
from customer_api import get_connection
from payments import _get_tariff_rate
from sms_outbound import send_gateway_sms

logger = logging.getLogger("cc-api.sms-payment-receipt")

SMS_PAYMENT_RECEIPT_ENABLED = os.environ.get(
    "SMS_PAYMENT_RECEIPT_ENABLED", "1",
).lower() in ("1", "true", "yes")


def _normalize_phone(raw: str) -> str:
    """Digits-only, at least 8 of them, or empty string."""
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    return digits if len(digits) >= 8 else ""


def _resolve_holder_phone(conn, account_number: str) -> str:
    """Account holder's phone from 1PDB customers, or empty string.

    COALESCE order mirrors ``low_balance_alerts.py``: cell_phone_1 first,
    then phone, then cell_phone_2.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(
                   NULLIF(TRIM(c.cell_phone_1), ''),
                   NULLIF(TRIM(c.phone), ''),
                   NULLIF(TRIM(c.cell_phone_2), '')
               ) AS phone
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE a.account_number = %s
        """,
        (account_number,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return _normalize_phone(row[0])
    return ""


def _recipient_for_receipt(holder: str, payer: str) -> str:
    """Prefer account holder; fall back to payer (sender)."""
    if holder:
        return holder
    return payer


def send_fee_payment_receipt_sms(
    account_number: str,
    payer_phone_raw: str,
    amount_paid_currency: float,
    fee_category: str,
) -> None:
    """Background task: one SMS after a connection or readyboard fee."""
    if not SMS_PAYMENT_RECEIPT_ENABLED:
        return
    payer = _normalize_phone(payer_phone_raw)
    try:
        with get_connection() as conn:
            holder = _resolve_holder_phone(conn, account_number)
    except Exception:
        holder = ""

    to = _recipient_for_receipt(holder, payer)
    if not to:
        logger.debug("Fee receipt SMS skipped — no holder or payer phone for %s", account_number)
        return

    sym = COUNTRY.currency_symbol
    fee_label = (
        "ho hokela" if fee_category == "connection_fee" else "readyboard"
    ) if COUNTRY.code == "LS" else (
        "connexion" if fee_category == "connection_fee" else "readyboard"
    )

    if COUNTRY.code == "BN":
        msg = (
            f"Paiement de {amount_paid_currency:,.0f} {sym} pour le compte "
            f"{account_number} ({fee_label}) enregistré."
        )
    else:
        msg = (
            f"Patala ea {sym}{amount_paid_currency:.2f} bakeng sa ntlo ea "
            f"{account_number} (tefo ea {fee_label}) e amohetsoe."
        )

    send_gateway_sms(
        to,
        msg,
        sms_type="balance",
        account_number=account_number,
        trigger="fee_receipt",
    )


def send_electricity_payment_receipt_sms(
    account_number: str,
    payer_phone_raw: str,
    amount_paid_currency: float,
) -> None:
    """Background task: one SMS after electricity payment commits."""
    if not SMS_PAYMENT_RECEIPT_ENABLED:
        return
    payer = _normalize_phone(payer_phone_raw)
    try:
        with get_connection() as conn:
            bal_kwh, _ = get_balance_kwh(conn, account_number)
            rate = _get_tariff_rate(conn, account_number)
            holder = _resolve_holder_phone(conn, account_number)
    except Exception:
        bal_kwh = 0.0
        rate = 0.0
        holder = ""

    to = _recipient_for_receipt(holder, payer)
    if not to:
        logger.debug("Payment receipt SMS skipped — no holder or payer phone for %s", account_number)
        return

    bal_curr = round(bal_kwh * rate, 2)
    sym = COUNTRY.currency_symbol

    if COUNTRY.code == "BN":
        msg = (
            f"Paiement {amount_paid_currency:,.0f} {sym} pour le compte "
            f"{account_number} enregistré. Solde: {bal_curr:,.0f} {sym} "
            f"({bal_kwh:.1f} kWh)."
        )
    else:
        msg = (
            f"Patala ea {sym}{amount_paid_currency:.2f} bakeng sa ntlo ea "
            f"{account_number} e amohetsoe. Saleng se setseng: "
            f"{sym}{bal_curr:.2f} ({bal_kwh:.1f} kWh)."
        )

    send_gateway_sms(
        to,
        msg,
        sms_type="balance",
        account_number=account_number,
        trigger="payment_receipt",
    )
