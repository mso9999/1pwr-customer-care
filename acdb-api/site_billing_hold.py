"""Site electricity-billing hold, with a per-meter billing override.

A site hold means installed meters supply power for free until inspection.
Consumption is still recorded. It is not subtracted from credit, and an
electricity payment keeps its currency and the tariff from the day it arrived.

A meter override bills that meter's account anyway, so one meter can be tested
while the rest of the site stays free. Hours before the override, inside the
site hold, stay free.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from country_config import COUNTRY, live_known_sites, live_site_abbrev
from middleware import effective_roles, raise_privilege_denied, require_employee
from models import CCRole, CurrentUser, UserType

logger = logging.getLogger("cc-api.site-billing-hold")

router = APIRouter(prefix="/api/site-billing-holds", tags=["site-billing-hold"])

_EDITOR_ROLES = {CCRole.superadmin.value, CCRole.onm_team.value}
_SCOPE_ALIASES = {"BJ": "BN", "BENIN": "BN", "LESOTHO": "LS", "ZAMBIA": "ZM"}
_SITE_RE = re.compile(r"([A-Za-z]{2,4})$")


def window_covers(hour: datetime, started: datetime, ended: Optional[datetime]) -> bool:
    if hour < started:
        return False
    if ended is not None and hour >= ended:
        return False
    return True


def hour_is_free(
    hour: datetime,
    site_windows: list[tuple[datetime, Optional[datetime]]],
    bill_windows: list[tuple[datetime, Optional[datetime]]],
) -> bool:
    """True when this hour was free supply and must stay out of the balance."""
    in_hold = any(window_covers(hour, started, ended) for started, ended in site_windows)
    in_bill = any(window_covers(hour, started, ended) for started, ended in bill_windows)
    return in_hold and not in_bill


def released_kwh(held_amount: float, rate: float) -> float:
    if held_amount <= 0 or rate <= 0:
        return 0.0
    return round(float(held_amount) / float(rate), 4)


def expected_site_confirmation(action: str, site_code: str) -> str:
    return f"{action.strip().upper()} {site_code.strip().upper()}"


def expected_meter_confirmation(action: str, meter_id: str) -> str:
    return f"{action.strip().upper()} {meter_id.strip().upper()}"


def vend_decision(
    *,
    effectively_held: bool,
    amount: float,
    rate: float,
    kwh_override: Optional[float] = None,
) -> tuple[float, Optional[float], str]:
    """Return kWh to vend, held currency or None, and the payment category."""
    if effectively_held and amount > 0:
        return 0.0, round(float(amount), 2), "electricity_held"
    if kwh_override is not None:
        kwh = round(float(kwh_override), 4)
    else:
        kwh = round(float(amount) / float(rate), 4) if rate and rate > 0 else 0.0
    return kwh, None, "electricity"


def _scope(code: str) -> str:
    text = (code or "").strip().upper()
    return _SCOPE_ALIASES.get(text, text)


def user_may_edit_site_billing(user: CurrentUser) -> bool:
    """Superadmin: any country. O&M: only their own. Finance may look, not change."""
    if user.user_type != UserType.employee:
        return False
    roles = set(effective_roles(user))
    if CCRole.superadmin.value in roles:
        return True
    if CCRole.onm_team.value not in roles:
        return False
    scoped = {_scope(code) for code in (user.scope_countries or [])}
    return COUNTRY.code.upper() in scoped


def _require_editor(user: CurrentUser) -> None:
    roles = set(effective_roles(user))
    if not roles.intersection(_EDITOR_ROLES):
        raise_privilege_denied(user, _EDITOR_ROLES, "change site electricity billing")
    if not user_may_edit_site_billing(user):
        raise HTTPException(
            status_code=403,
            detail="You can change electricity billing only for your own country.",
        )


def _savepoint(cur, name: str) -> None:
    cur.execute(f"SAVEPOINT {name}")


def _release(cur, name: str) -> None:
    cur.execute(f"RELEASE SAVEPOINT {name}")


def _rollback(cur, name: str) -> None:
    try:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
    except Exception:
        logger.exception("site billing hold savepoint rollback failed")


def account_site(cur, account_number: str) -> str:
    account_number = (account_number or "").strip().upper()
    try:
        _savepoint(cur, "site_hold_acct")
        cur.execute(
            """
            SELECT community
              FROM meters
             WHERE account_number = %s
               AND status = 'active'
               AND COALESCE(community, '') <> ''
             ORDER BY CASE WHEN role = 'primary' THEN 0 ELSE 1 END
             LIMIT 1
            """,
            (account_number,),
        )
        row = cur.fetchone()
        _release(cur, "site_hold_acct")
        if row and row[0]:
            return str(row[0]).strip().upper()
    except Exception:
        logger.exception("site lookup failed for %s", account_number)
        _rollback(cur, "site_hold_acct")
    match = _SITE_RE.search(account_number)
    return match.group(1).upper() if match else ""


def _open_site_hold(cur, site_code: str) -> Optional[dict]:
    try:
        _savepoint(cur, "site_hold_open")
        cur.execute(
            """
            SELECT id, site_code, started_at, reason
              FROM site_electricity_holds
             WHERE site_code = %s AND ended_at IS NULL
             LIMIT 1
            """,
            (site_code,),
        )
        row = cur.fetchone()
        _release(cur, "site_hold_open")
    except Exception:
        logger.exception("open site hold read failed for %s", site_code)
        _rollback(cur, "site_hold_open")
        return None
    if not row:
        return None
    return {"id": row[0], "site_code": row[1], "started_at": row[2], "reason": row[3]}


def account_has_bill_override(cur, account_number: str) -> bool:
    try:
        _savepoint(cur, "site_hold_meter")
        cur.execute(
            """
            SELECT 1
              FROM meter_electricity_overrides
             WHERE account_number = %s AND ended_at IS NULL
             LIMIT 1
            """,
            (account_number.strip().upper(),),
        )
        row = cur.fetchone()
        _release(cur, "site_hold_meter")
        return row is not None
    except Exception:
        logger.exception("meter override read failed for %s", account_number)
        _rollback(cur, "site_hold_meter")
        return False


def account_effectively_held(conn, account_number: str) -> bool:
    """True when this account's electricity is free right now."""
    cur = conn.cursor()
    site = account_site(cur, account_number)
    if not site or _open_site_hold(cur, site) is None:
        return False
    return not account_has_bill_override(cur, account_number)


def decide_vend(conn, account_number: str, amount: float, rate: float, kwh_override: Optional[float] = None):
    return vend_decision(
        effectively_held=account_effectively_held(conn, account_number),
        amount=float(amount or 0),
        rate=float(rate or 0),
        kwh_override=kwh_override,
    )


def stamp_held_payment(cur, txn_id: int, held_amount: float) -> None:
    """Mark a zero-kWh row so clearance can turn it into units. Missing columns are skipped."""
    try:
        _savepoint(cur, "site_hold_stamp")
        cur.execute(
            """
            UPDATE transactions
               SET payment_category = 'electricity_held',
                   held_electricity_amount = %s
             WHERE id = %s
            """,
            (held_amount, txn_id),
        )
        _release(cur, "site_hold_stamp")
    except Exception:
        logger.exception("could not stamp held electricity on txn %s", txn_id)
        _rollback(cur, "site_hold_stamp")


def consumption_sql_exclusion() -> str:
    """SQL fragment. Bind the account site code once, after the account number."""
    return """
          AND NOT (
                EXISTS (
                    SELECT 1 FROM site_electricity_holds h
                     WHERE h.site_code = COALESCE(NULLIF(UPPER(hourly_consumption.community), ''), %s)
                       AND hourly_consumption.reading_hour >= h.started_at
                       AND (h.ended_at IS NULL OR hourly_consumption.reading_hour < h.ended_at)
                )
            AND NOT EXISTS (
                    SELECT 1 FROM meter_electricity_overrides o
                     WHERE o.account_number = hourly_consumption.account_number
                       AND hourly_consumption.reading_hour >= o.started_at
                       AND (o.ended_at IS NULL OR hourly_consumption.reading_hour < o.ended_at)
                )
          )
    """


def release_held_payments(conn, site_code: str, started_at, ended_at) -> list[dict]:
    """Turn saved electricity into kWh at the tariff stored on each payment."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE transactions AS t
           SET kwh_value = ROUND(t.held_electricity_amount / NULLIF(t.rate_used, 0), 4),
               payment_category = 'electricity'
         WHERE t.payment_category = 'electricity_held'
           AND t.held_electricity_amount > 0
           AND t.transaction_date >= %s
           AND t.transaction_date < %s
           AND (
                EXISTS (
                    SELECT 1 FROM meters m
                     WHERE m.account_number = t.account_number
                       AND UPPER(m.community) = %s
                )
                OR UPPER(substring(t.account_number from '[A-Za-z]+$')) = %s
           )
        RETURNING t.id, t.account_number, t.kwh_value, t.held_electricity_amount
        """,
        (started_at, ended_at, site_code, site_code),
    )
    released = [
        {
            "transaction_id": row[0],
            "account_number": row[1],
            "kwh": float(row[2] or 0),
            "amount": float(row[3] or 0),
        }
        for row in cur.fetchall()
    ]
    accounts = {row["account_number"] for row in released}
    for account in accounts:
        try:
            _savepoint(cur, "site_hold_debt")
            from balance_engine import _sync_energy_debt
            _sync_energy_debt(conn, account, source="billing_hold_release")
            _release(cur, "site_hold_debt")
        except Exception:
            logger.exception("energy debt sync after release failed for %s", account)
            _rollback(cur, "site_hold_debt")
    return released


def credit_released_sparkmeter(released: list[dict]) -> None:
    """Push released units after the database commit. A push failure does not undo the release."""
    from payments import _credit_sm_sync, _meter_credit_enabled

    if not _meter_credit_enabled():
        return
    for row in released:
        if row["amount"] <= 0 or row["kwh"] <= 0:
            continue
        try:
            _credit_sm_sync(
                row["account_number"],
                row["amount"],
                f"site billing clearance txn {row['transaction_id']}",
                str(row["transaction_id"]),
            )
        except Exception:
            logger.exception("SparkMeter credit after clearance failed for %s", row["account_number"])


def _close_site_relays(conn, site_code: str) -> None:
    from relay_control import maybe_hold_close_relay

    cur = conn.cursor()
    try:
        _savepoint(cur, "site_hold_relays")
        cur.execute(
            """
            SELECT DISTINCT account_number
              FROM meters
             WHERE UPPER(community) = %s
               AND platform = 'prototype'
               AND status = 'active'
            """,
            (site_code,),
        )
        accounts = [row[0] for row in cur.fetchall()]
        _release(cur, "site_hold_relays")
    except Exception:
        logger.exception("could not list meters for hold close at %s", site_code)
        _rollback(cur, "site_hold_relays")
        return
    for account in accounts:
        if account_has_bill_override(cur, account):
            continue
        try:
            maybe_hold_close_relay(conn, account, reason="site_billing_hold")
        except Exception:
            logger.exception("hold close failed for %s", account)


def account_hold_display(conn, account_number: str) -> dict:
    """Fields for Customer Data and My Dashboard. Missing tables read as billing on."""
    cur = conn.cursor()
    held = account_effectively_held(conn, account_number)
    if not held:
        return {
            "electricity_billing_held": False,
            "held_electricity_currency": 0.0,
            "free_supply_kwh": 0.0,
        }
    site = account_site(cur, account_number)
    hold = _open_site_hold(cur, site) or {}
    started = hold.get("started_at")
    held_currency = 0.0
    free_kwh = 0.0
    try:
        _savepoint(cur, "site_hold_disp")
        cur.execute(
            """
            SELECT COALESCE(SUM(held_electricity_amount), 0)
              FROM transactions
             WHERE account_number = %s
               AND payment_category = 'electricity_held'
            """,
            (account_number.strip().upper(),),
        )
        held_currency = float(cur.fetchone()[0] or 0)
        if started is not None:
            cur.execute(
                """
                SELECT COALESCE(SUM(kwh), 0)
                  FROM hourly_consumption
                 WHERE account_number = %s
                   AND reading_hour >= %s
                """,
                (account_number.strip().upper(), started),
            )
            free_kwh = float(cur.fetchone()[0] or 0)
        _release(cur, "site_hold_disp")
    except Exception:
        logger.exception("hold display failed for %s", account_number)
        _rollback(cur, "site_hold_disp")
    return {
        "electricity_billing_held": True,
        "held_electricity_currency": round(held_currency, 2),
        "free_supply_kwh": round(free_kwh, 3),
    }


class HoldBody(BaseModel):
    confirmation: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=8, max_length=500)


class MeterBillingBody(BaseModel):
    mode: str = Field(..., description="'bill' or 'inherit'")
    confirmation: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=8, max_length=500)


def _sites_payload(conn, user: CurrentUser) -> dict:
    names = live_site_abbrev(COUNTRY.code)
    codes = sorted(live_known_sites(COUNTRY.code))
    cur = conn.cursor()
    holds: dict[str, dict] = {}
    billing_meters: dict[str, list] = {}
    try:
        _savepoint(cur, "site_hold_list")
        cur.execute(
            """
            SELECT site_code, started_at, reason
              FROM site_electricity_holds
             WHERE ended_at IS NULL
            """
        )
        for site_code, started_at, reason in cur.fetchall():
            holds[str(site_code).upper()] = {
                "started_at": started_at.isoformat() if started_at else None,
                "reason": reason,
            }
        cur.execute(
            """
            SELECT site_code, meter_id, account_number, started_at
              FROM meter_electricity_overrides
             WHERE ended_at IS NULL
            """
        )
        for site_code, meter_id, account_number, started_at in cur.fetchall():
            billing_meters.setdefault(str(site_code).upper(), []).append({
                "meter_id": meter_id,
                "account_number": account_number,
                "started_at": started_at.isoformat() if started_at else None,
            })
        _release(cur, "site_hold_list")
    except Exception:
        logger.exception("site billing hold list failed")
        _rollback(cur, "site_hold_list")
    sites = []
    for code in codes:
        hold = holds.get(code.upper())
        sites.append({
            "code": code,
            "name": names.get(code, code),
            "held": hold is not None,
            "started_at": None if hold is None else hold["started_at"],
            "reason": None if hold is None else hold["reason"],
            "billing_meters": billing_meters.get(code.upper(), []),
        })
    return {
        "country": COUNTRY.code,
        "can_edit": user_may_edit_site_billing(user),
        "sites": sites,
    }


@router.get("")
def list_site_billing_holds(user: CurrentUser = Depends(require_employee)):
    from customer_api import get_connection

    with get_connection() as conn:
        try:
            return _sites_payload(conn, user)
        finally:
            conn.rollback()


@router.post("/{site_code}/hold")
def start_site_hold(
    site_code: str,
    body: HoldBody,
    user: CurrentUser = Depends(require_employee),
):
    """Free supply for every meter at the site that is not already set to bill."""
    _require_editor(user)
    site_code = site_code.strip().upper()
    if site_code not in {code.upper() for code in live_known_sites(COUNTRY.code)}:
        raise HTTPException(status_code=404, detail="Unknown site for this country.")
    expected = expected_site_confirmation("HOLD", site_code)
    if body.confirmation.strip().upper() != expected:
        raise HTTPException(status_code=400, detail=f"Type {expected} exactly.")
    from customer_api import get_connection
    from mutations import log_mutation

    with get_connection() as conn:
        cur = conn.cursor()
        existing = _open_site_hold(cur, site_code)
        if existing:
            conn.rollback()
            raise HTTPException(status_code=409, detail="This site is already on an electricity hold.")
        try:
            cur.execute(
                """
                INSERT INTO site_electricity_holds (site_code, reason, started_by)
                VALUES (%s, %s, %s)
                RETURNING id, started_at
                """,
                (site_code, body.reason.strip(), str(user.user_id)),
            )
            hold_id, started_at = cur.fetchone()
            log_mutation(
                user,
                "create",
                "site_electricity_holds",
                site_code,
                new_values={"held": True, "reason": body.reason.strip(), "country": COUNTRY.code},
                metadata={"kind": "site_electricity_hold", "hold_id": hold_id},
                conn=conn,
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("start site hold failed for %s", site_code)
            raise HTTPException(status_code=500, detail="Could not start the site hold.") from exc
    with get_connection() as conn:
        try:
            _close_site_relays(conn, site_code)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("relay close after hold start failed for %s", site_code)
    return {"status": "ok", "site_code": site_code, "started_at": started_at.isoformat()}


@router.post("/{site_code}/bill")
def clear_site_hold(
    site_code: str,
    body: HoldBody,
    user: CurrentUser = Depends(require_employee),
):
    """End the hold and turn saved electricity payments into kWh."""
    _require_editor(user)
    site_code = site_code.strip().upper()
    expected = expected_site_confirmation("BILL", site_code)
    if body.confirmation.strip().upper() != expected:
        raise HTTPException(status_code=400, detail=f"Type {expected} exactly.")
    from customer_api import get_connection
    from mutations import log_mutation

    released: list[dict] = []
    with get_connection() as conn:
        cur = conn.cursor()
        existing = _open_site_hold(cur, site_code)
        if not existing:
            conn.rollback()
            raise HTTPException(status_code=409, detail="This site is already billing.")
        try:
            cur.execute(
                """
                UPDATE site_electricity_holds
                   SET ended_at = NOW(), ended_by = %s
                 WHERE id = %s AND ended_at IS NULL
                RETURNING started_at, ended_at
                """,
                (str(user.user_id), existing["id"]),
            )
            started_at, ended_at = cur.fetchone()
            released = release_held_payments(conn, site_code, started_at, ended_at)
            log_mutation(
                user,
                "update",
                "site_electricity_holds",
                site_code,
                old_values={"held": True, "reason": existing["reason"]},
                new_values={
                    "held": False,
                    "reason": body.reason.strip(),
                    "released_payments": len(released),
                    "country": COUNTRY.code,
                },
                metadata={"kind": "site_electricity_bill"},
                conn=conn,
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("clear site hold failed for %s", site_code)
            raise HTTPException(status_code=500, detail="Could not turn billing on. Nothing was released.") from exc
    credit_released_sparkmeter(released)
    return {
        "status": "ok",
        "site_code": site_code,
        "released_payments": len(released),
        "released_kwh": round(sum(row["kwh"] for row in released), 4),
    }


@router.post("/meters/{meter_id}")
def set_meter_billing(
    meter_id: str,
    body: MeterBillingBody,
    user: CurrentUser = Depends(require_employee),
):
    """Bill one meter while its site is still on an inspection hold, or return it to the site rule."""
    _require_editor(user)
    mode = body.mode.strip().lower()
    if mode not in ("bill", "inherit"):
        raise HTTPException(status_code=400, detail="mode must be bill or inherit.")
    meter_id = meter_id.strip()
    action = "BILL" if mode == "bill" else "FOLLOW"
    expected = expected_meter_confirmation(action, meter_id)
    if body.confirmation.strip().upper() != expected:
        raise HTTPException(status_code=400, detail=f"Type {expected} exactly.")
    from customer_api import get_connection
    from mutations import log_mutation

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT meter_id, account_number, UPPER(COALESCE(community, ''))
                  FROM meters
                 WHERE UPPER(meter_id) = UPPER(%s)
                 LIMIT 1
                """,
                (meter_id,),
            )
            meter = cur.fetchone()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=500, detail="Could not read the meter.") from exc
        if not meter:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Meter not found.")
        canonical_id, account_number, site_code = meter
        if site_code and site_code not in {code.upper() for code in live_known_sites(COUNTRY.code)}:
            conn.rollback()
            raise HTTPException(status_code=403, detail="That meter is not in this country.")
        try:
            cur.execute(
                """
                SELECT id FROM meter_electricity_overrides
                 WHERE UPPER(meter_id) = UPPER(%s) AND ended_at IS NULL
                 LIMIT 1
                """,
                (canonical_id,),
            )
            open_row = cur.fetchone()
            if mode == "bill":
                if open_row:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="This meter is already set to billing.")
                cur.execute(
                    """
                    INSERT INTO meter_electricity_overrides
                        (meter_id, account_number, site_code, reason, started_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (canonical_id, account_number, site_code, body.reason.strip(), str(user.user_id)),
                )
                override_id = cur.fetchone()[0]
                new_values = {"mode": "bill", "meter_id": canonical_id, "account_number": account_number}
            else:
                if not open_row:
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="This meter already follows the site.")
                cur.execute(
                    """
                    UPDATE meter_electricity_overrides
                       SET ended_at = NOW(), ended_by = %s
                     WHERE id = %s
                    """,
                    (str(user.user_id), open_row[0]),
                )
                override_id = open_row[0]
                new_values = {"mode": "inherit", "meter_id": canonical_id, "account_number": account_number}
            log_mutation(
                user,
                "update",
                "meter_electricity_overrides",
                str(canonical_id),
                new_values={**new_values, "reason": body.reason.strip(), "country": COUNTRY.code},
                metadata={"kind": "meter_electricity_billing", "override_id": override_id},
                conn=conn,
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("meter billing override failed for %s", meter_id)
            raise HTTPException(status_code=500, detail="Could not change meter billing.") from exc
    return {"status": "ok", "meter_id": canonical_id, "mode": mode, "account_number": account_number}
