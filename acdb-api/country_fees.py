"""
Country fee configuration for one-off charges (connection / readyboard).

These fees are *amounts*, not rates -- a customer pays exactly the connection
fee (501 LSL in Lesotho) once, in currency. They are stored in
``system_config`` so finance / O&M / superadmin can edit them without a code
deploy. ``country_config.py`` holds only the seed defaults.

Endpoints (all under /api/admin):
    GET  /api/admin/country-fees   — connection/readyboard fees, low-balance kWh
                                     thresholds, SMS gateway balance callback caps,
                                     and low-balance SMS daily cap for **this** country's DB + currency
    PUT  /api/admin/country-fees   — update any subset (superadmin / onm_team / finance_team)

Each CC backend (Lesotho vs Benin) has its own ``system_config`` — so O&M sets e.g.
10 kWh / 20 kWh clear on LS and 5 kWh / 12 kWh on BN independently.

The fee_classifier reads fee values to decide whether an inbound payment is
a one-off connection / readyboard fee or normal kWh credit. See
``acdb-api/fee_classifier.py``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from country_config import COUNTRY
from customer_api import get_connection
from middleware import require_employee
from models import CCRole, CurrentUser
from mutations import try_log_mutation

logger = logging.getLogger("cc-api.country-fees")

router = APIRouter(prefix="/api/admin/country-fees", tags=["admin", "country-fees"])

_FEE_ADMIN_ROLES = {
    CCRole.superadmin.value,
    CCRole.onm_team.value,
    CCRole.finance_team.value,
}


def _require_fee_admin(user: CurrentUser) -> None:
    if user.role not in _FEE_ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Country fee management requires superadmin, onm_team, or finance_team",
        )


# ---------------------------------------------------------------------------
# Helper: read live values
# ---------------------------------------------------------------------------

_KEYS = ("connection_fee_amount", "readyboard_fee_amount")


def _read_system_float(conn, key: str, fallback: float) -> float:
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
    row = cur.fetchone()
    if not row:
        return fallback
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return fallback


def _read_system_int(
    conn,
    key: str,
    fallback: int,
    *,
    minimum: int = 1,
    maximum: int = 10_000,
) -> int:
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
    row = cur.fetchone()
    if not row or row[0] is None or str(row[0]).strip() == "":
        return fallback
    try:
        v = int(float(row[0]))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, v))


def _read_fee_amount(conn, key: str, fallback: float) -> float:
    return _read_system_float(conn, key, fallback)


def get_low_balance_thresholds(conn) -> tuple[float, float]:
    """Warn/clear kWh thresholds for ``low_balance_alerts`` (single source of truth)."""
    warn = _read_system_float(
        conn,
        "low_balance_kwh_threshold",
        COUNTRY.default_low_balance_kwh_threshold,
    )
    clear = _read_system_float(
        conn,
        "low_balance_kwh_clear",
        COUNTRY.default_low_balance_kwh_clear,
    )
    if clear <= warn:
        clear = warn + 5.0
    return warn, clear


def get_sms_rate_limit_settings(conn) -> dict:
    """SMS caps: gateway balance callbacks + low-balance alert frequency (system_config)."""
    return {
        "sms_balance_reply_max_per_hour": _read_system_int(
            conn, "sms_balance_reply_max_per_hour", 1, minimum=1, maximum=500,
        ),
        "sms_balance_reply_max_per_day": _read_system_int(
            conn, "sms_balance_reply_max_per_day", 3, minimum=1, maximum=5000,
        ),
        "low_balance_alert_max_per_day": _read_system_int(
            conn, "low_balance_alert_max_per_day", 2, minimum=1, maximum=50,
        ),
    }


def get_country_fees(conn) -> dict:
    """Return the live (connection_fee, readyboard_fee) for the active country.

    Importable by other modules (notably fee_classifier) so it stays a single
    source of truth.
    """
    lb_warn, lb_clear = get_low_balance_thresholds(conn)
    sms_rates = get_sms_rate_limit_settings(conn)
    return {
        "connection_fee_amount": _read_fee_amount(
            conn, "connection_fee_amount", COUNTRY.default_connection_fee
        ),
        "readyboard_fee_amount": _read_fee_amount(
            conn, "readyboard_fee_amount", COUNTRY.default_readyboard_fee
        ),
        "low_balance_kwh_threshold": lb_warn,
        "low_balance_kwh_clear": lb_clear,
        "sms_balance_reply_max_per_hour": sms_rates["sms_balance_reply_max_per_hour"],
        "sms_balance_reply_max_per_day": sms_rates["sms_balance_reply_max_per_day"],
        "low_balance_alert_max_per_day": sms_rates["low_balance_alert_max_per_day"],
        "currency": COUNTRY.currency,
        "currency_symbol": COUNTRY.currency_symbol,
        "country_code": COUNTRY.code,
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CountryFeesUpdate(BaseModel):
    connection_fee_amount: Optional[float] = Field(
        None, ge=0, description="Country connection fee. 0 disables auto-classification."
    )
    readyboard_fee_amount: Optional[float] = Field(
        None, ge=0, description="Country readyboard fee. 0 disables auto-classification."
    )
    low_balance_kwh_threshold: Optional[float] = Field(
        None,
        gt=0,
        description="SMS low-balance warning when remaining kWh is at or below this (country-specific).",
    )
    low_balance_kwh_clear: Optional[float] = Field(
        None,
        gt=0,
        description="Clear the “already warned” flag when balance rises to this kWh (must exceed threshold).",
    )
    sms_balance_reply_max_per_hour: Optional[int] = Field(
        None,
        ge=1,
        le=500,
        description="Max successful balance API lookups per account/phone per rolling hour (SMS gateway).",
    )
    sms_balance_reply_max_per_day: Optional[int] = Field(
        None,
        ge=1,
        le=5000,
        description="Max balance API lookups per account/phone per local calendar day.",
    )
    low_balance_alert_max_per_day: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="Max low-balance warning SMS per account per local calendar day.",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
def read_country_fees(user: CurrentUser = Depends(require_employee)):
    """Return the live connection/readyboard fee values for the active country."""
    with get_connection() as conn:
        return get_country_fees(conn)


@router.put("")
def update_country_fees(
    payload: CountryFeesUpdate,
    user: CurrentUser = Depends(require_employee),
):
    """Update one or both fee amounts. Role-gated."""
    _require_fee_admin(user)

    if (
        payload.connection_fee_amount is None
        and payload.readyboard_fee_amount is None
        and payload.low_balance_kwh_threshold is None
        and payload.low_balance_kwh_clear is None
        and payload.sms_balance_reply_max_per_hour is None
        and payload.sms_balance_reply_max_per_day is None
        and payload.low_balance_alert_max_per_day is None
    ):
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    updates: list[tuple[str, float]] = []
    int_updates: list[tuple[str, int]] = []
    if payload.connection_fee_amount is not None:
        updates.append(("connection_fee_amount", float(payload.connection_fee_amount)))
    if payload.readyboard_fee_amount is not None:
        updates.append(("readyboard_fee_amount", float(payload.readyboard_fee_amount)))

    with get_connection() as conn:
        old = get_country_fees(conn)

        new_warn = (
            float(payload.low_balance_kwh_threshold)
            if payload.low_balance_kwh_threshold is not None
            else old["low_balance_kwh_threshold"]
        )
        new_clear = (
            float(payload.low_balance_kwh_clear)
            if payload.low_balance_kwh_clear is not None
            else old["low_balance_kwh_clear"]
        )
        if payload.low_balance_kwh_threshold is not None or payload.low_balance_kwh_clear is not None:
            if new_clear <= new_warn:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"low_balance_kwh_clear ({new_clear}) must be greater than "
                        f"low_balance_kwh_threshold ({new_warn})"
                    ),
                )

        if payload.low_balance_kwh_threshold is not None:
            updates.append(("low_balance_kwh_threshold", new_warn))
        if payload.low_balance_kwh_clear is not None:
            updates.append(("low_balance_kwh_clear", new_clear))

        if payload.sms_balance_reply_max_per_hour is not None:
            int_updates.append(
                ("sms_balance_reply_max_per_hour", int(payload.sms_balance_reply_max_per_hour)),
            )
        if payload.sms_balance_reply_max_per_day is not None:
            int_updates.append(
                ("sms_balance_reply_max_per_day", int(payload.sms_balance_reply_max_per_day)),
            )
        if payload.low_balance_alert_max_per_day is not None:
            int_updates.append(
                ("low_balance_alert_max_per_day", int(payload.low_balance_alert_max_per_day)),
            )

        cur = conn.cursor()
        for key, value in updates:
            cur.execute(
                """
                INSERT INTO system_config (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, str(value)),
            )
        for key, value in int_updates:
            cur.execute(
                """
                INSERT INTO system_config (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, str(int(value))),
            )

        new = get_country_fees(conn)

        try_log_mutation(
            user,
            "update",
            "system_config",
            "country_fees",
            old_values={
                "connection_fee_amount": old["connection_fee_amount"],
                "readyboard_fee_amount": old["readyboard_fee_amount"],
                "low_balance_kwh_threshold": old["low_balance_kwh_threshold"],
                "low_balance_kwh_clear": old["low_balance_kwh_clear"],
                "sms_balance_reply_max_per_hour": old.get("sms_balance_reply_max_per_hour"),
                "sms_balance_reply_max_per_day": old.get("sms_balance_reply_max_per_day"),
                "low_balance_alert_max_per_day": old.get("low_balance_alert_max_per_day"),
            },
            new_values={
                "connection_fee_amount": new["connection_fee_amount"],
                "readyboard_fee_amount": new["readyboard_fee_amount"],
                "low_balance_kwh_threshold": new["low_balance_kwh_threshold"],
                "low_balance_kwh_clear": new["low_balance_kwh_clear"],
                "sms_balance_reply_max_per_hour": new.get("sms_balance_reply_max_per_hour"),
                "sms_balance_reply_max_per_day": new.get("sms_balance_reply_max_per_day"),
                "low_balance_alert_max_per_day": new.get("low_balance_alert_max_per_day"),
            },
            metadata={
                "kind": "country_fees_update",
                "endpoint": "PUT /api/admin/country-fees",
                "country_code": COUNTRY.code,
            },
            conn=conn,
        )
        conn.commit()
        return new
