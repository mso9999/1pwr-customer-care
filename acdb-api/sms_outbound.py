"""
Outbound customer SMS via the national gateway (PHP ``generate_and_send.php``).

Uses ``SMS_SERVER_URL`` (same as contract SMS). Country-aware MSISDN formatting
via ``country_config.COUNTRY.dial_code``.
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


def send_gateway_sms(
    phone_raw: str,
    message: str,
    *,
    sms_type: str = "balance",
    dial_code: str | None = None,
) -> bool:
    """GET request to ``generate_and_send.php`` (Medic gateway pattern).

    *sms_type* is forwarded as ``type=`` (e.g. ``welcome``, ``balance``).
    """
    if not SMS_SERVER_URL:
        logger.warning("SMS_SERVER_URL not set — skipping outbound SMS")
        return False
    number = format_phone_for_sms_gateway(phone_raw, dial_code)
    if len(number) < 10:
        logger.warning("Outbound SMS: unusable phone after normalize: %r", phone_raw)
        return False
    base = SMS_SERVER_URL.rstrip("/")
    url = (
        f"{base}/generate_and_send.php"
        f"?message={quote(message)}&type={quote(sms_type)}&number={number}"
    )
    try:
        requests.get(url, timeout=20)
        logger.info("Outbound SMS dispatched type=%s to %s", sms_type, number)
        return True
    except Exception as exc:
        logger.error("Outbound SMS failed for %s: %s", number, exc)
        return False
