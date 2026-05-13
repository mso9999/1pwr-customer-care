"""Rate limits for SMS gateway balance callbacks (GET .../gateway/balance*)."""

from __future__ import annotations

import logging

from country_config import COUNTRY
from country_fees import get_sms_rate_limit_settings

logger = logging.getLogger("cc-api.sms-gateway-rate")


def _normalize_rate_key_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    return digits if len(digits) >= 5 else "invalid"


def balance_gateway_rate_key_for_account(account_number: str) -> str:
    return f"acct:{str(account_number).strip().upper()}"


def balance_gateway_rate_key_for_phone(phone: str) -> str:
    return f"phone:{_normalize_rate_key_phone(phone)}"


def enforce_balance_gateway_rate_limit(conn, rate_key: str) -> None:
    """Raise fastapi.HTTPException(429) if hour or day limit exceeded."""
    from fastapi import HTTPException

    limits = get_sms_rate_limit_settings(conn)
    max_h = limits["sms_balance_reply_max_per_hour"]
    max_d = limits["sms_balance_reply_max_per_day"]
    tz = COUNTRY.timezone

    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*) FROM sms_gateway_balance_rate_log
        WHERE rate_key = %s AND requested_at > NOW() - INTERVAL '1 hour'
        """,
        (rate_key,),
    )
    hour_n = int(cur.fetchone()[0] or 0)
    cur.execute(
        """
        SELECT COUNT(*) FROM sms_gateway_balance_rate_log
        WHERE rate_key = %s
          AND date(timezone(%s, requested_at)) = date(timezone(%s, NOW()))
        """,
        (rate_key, tz, tz),
    )
    day_n = int(cur.fetchone()[0] or 0)

    if hour_n >= max_h:
        logger.info("Balance gateway rate limit (hour): key=%s count=%s max=%s", rate_key, hour_n, max_h)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "scope": "per_hour",
                "max": max_h,
                "retry_after_seconds": 3600,
            },
        )
    if day_n >= max_d:
        logger.info("Balance gateway rate limit (day): key=%s count=%s max=%s", rate_key, day_n, max_d)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "scope": "per_day",
                "max": max_d,
                "retry_after_seconds": 86400,
            },
        )


def record_balance_gateway_request(conn, rate_key: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sms_gateway_balance_rate_log (rate_key) VALUES (%s)",
        (rate_key,),
    )
