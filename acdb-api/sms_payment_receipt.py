"""Post-success SMS with payment acknowledgement and 1PDB balance.

Receipt SMS always goes to the payer's phone.  If the account holder has a
valid phone on record that differs from the payer's, a copy is also sent to
the account holder so they get confirmation when a family member or agent
made the payment on their behalf.
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


def send_fee_payment_receipt_sms(
    account_number: str,
    payer_phone_raw: str,
    amount_paid_currency: float,
    fee_category: str,
) -> None:
    """Background task: SMS payer after a connection or readyboard fee.

    If the account holder has a valid phone on record that differs from the
    payer, a copy is also sent to the account holder.
    """
    if not SMS_PAYMENT_RECEIPT_ENABLED:
        return
    payer = _normalize_phone(payer_phone_raw)
    if not payer:
        logger.debug("Fee receipt SMS skipped — no usable payer phone for %s", account_number)
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

    send_gateway_sms(payer, msg, sms_type="balance",
                     account_number=account_number,
                     trigger="fee_receipt")

    try:
        with get_connection() as conn:
            holder = _resolve_holder_phone(conn, account_number)
    except Exception:
        holder = ""
    if holder and holder != payer:
        send_gateway_sms(holder, msg, sms_type="balance",
                         account_number=account_number,
                         trigger="fee_receipt_to_holder")


def send_electricity_payment_receipt_sms(
    account_number: str,
    payer_phone_raw: str,
    amount_paid_currency: float,
) -> None:
    """Background task: SMS payer after electricity payment commits.

    If the account holder has a valid phone on record that differs from the
    payer, a copy is also sent to the account holder.
    """
    if not SMS_PAYMENT_RECEIPT_ENABLED:
        return
    payer = _normalize_phone(payer_phone_raw)
    if not payer:
        logger.debug("Payment receipt SMS skipped — no usable payer phone for %s", account_number)
        return

    try:
        with get_connection() as conn:
            bal_kwh, _ = get_balance_kwh(conn, account_number)
            rate = _get_tariff_rate(conn, account_number)
            holder = _resolve_holder_phone(conn, account_number)
    except Exception:
        bal_kwh = 0.0
        rate = 0.0
        holder = ""

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

    send_gateway_sms(payer, msg, sms_type="balance",
                     account_number=account_number,
                     trigger="payment_receipt")

    if holder and holder != payer:
        send_gateway_sms(holder, msg, sms_type="balance",
                         account_number=account_number,
                         trigger="payment_receipt_to_holder")
