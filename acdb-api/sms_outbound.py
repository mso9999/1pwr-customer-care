"""
Outbound customer SMS via the national gateway (PHP ``generate_and_send.php``).

Uses ``SMS_SERVER_URL`` (same as contract SMS). Country-aware MSISDN formatting
via ``country_config.COUNTRY.dial_code``. Every send attempt is logged to
``sms_outbound_log``.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import requests

from country_config import COUNTRY

logger = logging.getLogger("cc-api.sms-outbound")

SMS_SERVER_URL = os.environ.get("SMS_SERVER_URL")


def format_phone_for_sms_gateway(phone: str, dial_code: str | None = None) -> str:
    """Normalize handset for CM.com / gateway (digits only, international)."""
    dc = (dial_code or COUNTRY.dial_code).strip()
    digits = "".join(c for c in str(phone) if c.isdigit())
    if not digits:
        return digits
    if digits.startswith(dc):
        return digits
    stripped = digits.lstrip("0")
    # Lesotho national mobile without country code (common in 1PDB)
    if dc == "266" and len(stripped) == 8:
        return dc + stripped
    return dc + stripped


def _log_sms(
    *,
    sms_type: str,
    phone_raw: str,
    phone_normalized: str,
    message: str,
    success: bool,
    error: str | None,
    account_number: str | None,
    trigger_ctx: str,
    gateway_url: str,
) -> None:
    """Write a row to sms_outbound_log. Opens its own short-lived connection."""
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO sms_outbound_log
                      (sms_type, phone_raw, phone_normalized, message,
                       success, error, account_number, trigger_ctx, gateway_url)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    sms_type, phone_raw, phone_normalized, message,
                    success, error, account_number, trigger_ctx, gateway_url,
                ),
            )
            conn.commit()
    except Exception:
        logger.exception("Failed to write sms_outbound_log row")


def send_gateway_sms(
    phone_raw: str,
    message: str,
    *,
    sms_type: str = "balance",
    dial_code: str | None = None,
    account_number: str | None = None,
    trigger: str = "",
) -> bool:
    """GET request to ``generate_and_send.php`` (Medic gateway pattern).

    *sms_type* is forwarded as ``type=`` (e.g. ``welcome``, ``balance``).
    Every attempt is logged to ``sms_outbound_log``.
    """
    phone_normalized = format_phone_for_sms_gateway(phone_raw, dial_code)
    base = (SMS_SERVER_URL or "").rstrip("/")
    gateway_url = base if base else ""

    if not SMS_SERVER_URL:
        logger.warning("SMS_SERVER_URL not set — skipping outbound SMS")
        _log_sms(
            sms_type=sms_type, phone_raw=str(phone_raw),
            phone_normalized=phone_normalized, message=message,
            success=False, error="SMS_SERVER_URL not set",
            account_number=account_number, trigger_ctx=trigger,
            gateway_url=gateway_url,
        )
        return False
    if len(phone_normalized) < 10:
        logger.warning("Outbound SMS: unusable phone after normalize: %r", phone_raw)
        _log_sms(
            sms_type=sms_type, phone_raw=str(phone_raw),
            phone_normalized=phone_normalized, message=message,
            success=False, error="unusable phone after normalize",
            account_number=account_number, trigger_ctx=trigger,
            gateway_url=gateway_url,
        )
        return False
    url = (
        f"{base}/generate_and_send.php"
        f"?message={quote(message)}&type={quote(sms_type)}&number={phone_normalized}"
    )
    try:
        requests.get(url, timeout=20)
        logger.info("Outbound SMS dispatched type=%s to %s", sms_type, phone_normalized)
        _log_sms(
            sms_type=sms_type, phone_raw=str(phone_raw),
            phone_normalized=phone_normalized, message=message,
            success=True, error=None,
            account_number=account_number, trigger_ctx=trigger,
            gateway_url=gateway_url,
        )
        return True
    except Exception as exc:
        logger.error("Outbound SMS failed for %s: %s", phone_normalized, exc)
        _log_sms(
            sms_type=sms_type, phone_raw=str(phone_raw),
            phone_normalized=phone_normalized, message=message,
            success=False, error=str(exc),
            account_number=account_number, trigger_ctx=trigger,
            gateway_url=gateway_url,
        )
        return False
