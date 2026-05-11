"""Post-success SMS to payer with payment acknowledgement and 1PDB balance."""

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


def send_electricity_payment_receipt_sms(
    account_number: str,
    payer_phone_raw: str,
    amount_paid_currency: float,
) -> None:
    """Background task: SMS payer after electricity-path payment commits to 1PDB."""
    if not SMS_PAYMENT_RECEIPT_ENABLED:
        return
    digits = "".join(c for c in str(payer_phone_raw) if c.isdigit())
    if len(digits) < 8:
        logger.debug(
            "Payment receipt SMS skipped — no usable payer phone for %s",
            account_number,
        )
        return
    try:
        with get_connection() as conn:
            bal_kwh, _ = get_balance_kwh(conn, account_number)
            rate = _get_tariff_rate(conn, account_number)
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

        send_gateway_sms(digits, msg, sms_type="balance",
                         account_number=account_number,
                         trigger="payment_receipt")
    except Exception as e:
        logger.warning(
            "Payment receipt SMS failed for acct=%s: %s",
            account_number,
            e,
        )
