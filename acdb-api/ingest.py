"""
Real-time data ingestion and meter management endpoints for 1PDB.

Provides:
  - POST  /api/meters/reading                — prototype meter readings (from ingestion_gate Lambda)
  - GET   /api/meters/account/{account}      — list meters + roles for an account
  - PATCH /api/meters/{meter_id}/role        — change meter role (primary/check/backup)
  - POST  /api/sms/incoming                  — mirrored SMS (LS: M-Pesa + EcoCash → 1PDB then Koios; BN: MoMo when COUNTRY_CODE=BN)
  - POST  /api/bn/sms/incoming               — same handler (public URL for Benin gateway behind /api/bn)

Meter roles:
  - primary: billing/production meter, used in consumption aggregation
  - check:   verification meter in series with primary, data stored but excluded from aggregates
  - backup:  standby meter, not currently active

During 1Meter testing, prototype meters are registered as 'check' alongside
the existing SparkMeter (primary).  When a 1Meter graduates to production,
use PATCH .../role to promote it — the old primary is auto-demoted.
"""

import json
import logging
import os
import re

import psycopg2
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests as http_requests
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from country_config import COUNTRY, KOIOS_SITES, UTC_OFFSET_HOURS
from cc_bridge_notify import notify_cc_bridge
from customer_api import get_connection
from momo_bj import parse_momo_bn_sms, resolve_bn_momo_account
from mpesa_sms import mpesa_receipt_in_use, parse_ls_sms_payment, resolve_sms_account
from sparkmeter_credit import credit_sparkmeter

logger = logging.getLogger("cc-api.ingest")

# If true (default), push M-PESA payments recorded via /api/sms/incoming to SparkMeter/Koios
# after 1PDB commit. Set false only if another system (e.g. legacy PHP) already credits SM
# for every SMS and you observe double credits.
SMS_INGEST_PUSH_SPARKMETER = os.environ.get("SMS_INGEST_PUSH_SPARKMETER", "1").lower() in (
    "1", "true", "yes",
)

_METER_TZ = timezone(timedelta(hours=UTC_OFFSET_HOURS))

router = APIRouter(tags=["ingest"])


def _watts_to_kw(value: float) -> float:
    """Normalize 1Meter active-power payloads from W to kW for storage."""
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------------------
# Koios consumption sync (triggered by payment events)
# ---------------------------------------------------------------------------

KOIOS_BASE = "https://www.sparkmeter.cloud"
# Per-country org + read keys (consumption sync after SMS payment)
_CC = COUNTRY.code
KOIOS_ORG = COUNTRY.koios_org_id
KOIOS_KEY = os.environ.get(f"KOIOS_API_KEY_{_CC}") or os.environ.get(
    "KOIOS_API_KEY", "SGWcnZpgCj-R0fGoVRtjbwMcElV7BvZGz00EEmJDv54"
)
KOIOS_SECRET = os.environ.get(f"KOIOS_API_SECRET_{_CC}") or os.environ.get(
    "KOIOS_API_SECRET", "gJ5gHPsw21W8Jwl&!aId9O5uoywpg#2G"
)

_sync_lock = threading.Lock()


def _fetch_koios_readings(site_id: str, date_from: str, date_to: str) -> list:
    """Fetch readings from Koios v2 historical API for a site/date range."""
    session = http_requests.Session()
    session.headers.update({"X-API-KEY": KOIOS_KEY, "X-API-SECRET": KOIOS_SECRET})
    all_data, cursor = [], None
    while True:
        body = {
            "filters": {
                "sites": [site_id],
                "date_range": {"from": date_from, "to": date_to},
            },
            "per_page": 1000,
        }
        if cursor:
            body["cursor"] = cursor
        for attempt in range(3):
            try:
                r = session.post(
                    f"{KOIOS_BASE}/api/v2/organizations/{KOIOS_ORG}/data/historical",
                    json=body, timeout=120,
                )
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except (http_requests.exceptions.ReadTimeout,
                    http_requests.exceptions.ConnectionError):
                time.sleep(5 * (attempt + 1))
                continue
        else:
            return all_data

        resp = r.json()
        all_data.extend(resp.get("data", []))
        pag = resp.get("pagination", {})
        cursor = pag.get("cursor")
        if not pag.get("has_more") or not cursor:
            break
        time.sleep(0.3)
    return all_data


def _bin_to_hourly(records: list) -> list[tuple]:
    """Bin Koios readings to hourly (meter_serial, customer_code, hour_str, kwh)."""
    hourly: dict[tuple, float] = defaultdict(float)
    for rec in records:
        kwh = rec.get("kilowatt_hours", 0) or 0
        if kwh <= 0:
            continue
        meter = rec.get("meter", {})
        serial = meter.get("serial_number", "")
        customer = meter.get("customer", {})
        code = customer.get("code", "") or ""
        ts_str = rec.get("timestamp", "")
        if not ts_str or not serial:
            continue
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        hour_str = dt.strftime("%Y-%m-%d %H:00:00+00")
        hourly[(serial, code, hour_str)] += kwh
    return [(s, c, h, round(k, 4)) for (s, c, h), k in hourly.items()]


def sync_consumption_for_site(community: str):
    """Fetch last 2 days of Koios consumption for a site and insert into 1PDB."""
    site_id = KOIOS_SITES.get(community)
    if not site_id:
        logger.debug("sync_consumption: unknown community %s", community)
        return

    if not _sync_lock.acquire(blocking=False):
        logger.debug("sync_consumption: skipped (another sync running)")
        return
    try:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info("sync_consumption: fetching %s (%s) %s..%s",
                     community, site_id[:8], yesterday, today)

        raw = _fetch_koios_readings(site_id, yesterday, today)
        if not raw:
            logger.info("sync_consumption: %s — no data", community)
            return

        hourly = _bin_to_hourly(raw)
        if not hourly:
            logger.info("sync_consumption: %s — no hourly bins", community)
            return

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT meter_id, account_number, community FROM meters")
            meter_map = {
                r[0]: {"acct": r[1] or "", "comm": r[2] or ""}
                for r in cur.fetchall()
            }

            import psycopg2.extras
            batch = []
            for serial, code, hour_str, kwh in hourly:
                acct = code or meter_map.get(serial, {}).get("acct", serial)
                comm = meter_map.get(serial, {}).get("comm", community)
                batch.append((acct, serial, hour_str, kwh, comm, "koios"))

            if batch:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO hourly_consumption
                        (account_number, meter_id, reading_hour, kwh, community, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meter_id, reading_hour) DO NOTHING
                """, batch, page_size=500)
                conn.commit()

            logger.info("sync_consumption: %s — %d hourly records synced", community, len(batch))
    except Exception as e:
        logger.error("sync_consumption failed for %s: %s", community, e)
    finally:
        _sync_lock.release()

IOT_KEY = os.environ.get("IOT_INGEST_KEY", "1pwr-iot-ingest-2026")

def _resolve_meter(conn, raw_id: str) -> tuple[str, str | None, str | None]:
    """Resolve a meter ID to (canonical_id, account_number, community).

    IoT Core sends 12-digit padded IDs (e.g. '000023022673') while the
    meters table uses short IDs ('23022673').  Try both forms.
    Returns (meter_id, account_number, community) — account/community are None
    if not found.
    """
    cur = conn.cursor()
    stripped = raw_id.lstrip("0") or raw_id
    candidates = list({raw_id, stripped})

    cur.execute(
        "SELECT meter_id, account_number, community FROM meters "
        "WHERE meter_id = ANY(%s) AND platform = 'prototype' LIMIT 1",
        (candidates,),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1], row[2]
    return raw_id, None, None


# ---------------------------------------------------------------------------
# Prototype meter reading ingestion (from ingestion_gate Lambda)
# ---------------------------------------------------------------------------

class MeterReading(BaseModel):
    meter_id: str
    timestamp: str            # YYYYMMDDHHMM
    energy_active: float = 0
    energy_integrated: Optional[float] = None
    power_active: float = 0
    voltage: float = 0
    current: float = 0
    relay: str = "0"
    frequency: float = 0
    # Set by onepwr-aws-mesh MQTT payload once published; forwarded by ingestion_gate Lambda.
    firmware_version: Optional[str] = None


@router.post("/api/meters/reading")
def ingest_meter_reading(reading: MeterReading, x_iot_key: str = Header(None)):
    if x_iot_key != IOT_KEY:
        raise HTTPException(status_code=403, detail="Invalid IoT key")

    try:
        ts = datetime.strptime(reading.timestamp, "%Y%m%d%H%M").replace(
            tzinfo=_METER_TZ).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad timestamp format: {reading.timestamp}")

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            meter_id, account, community = _resolve_meter(conn, reading.meter_id)
            if not account:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown prototype meter: {reading.meter_id}",
                )

            cur.execute(
                "SELECT last_energy_kwh FROM prototype_meter_state WHERE meter_id = %s",
                (meter_id,),
            )
            row = cur.fetchone()
            prev_energy = float(row[0]) if row else None

            # Use energy_active (DDS8888 Modbus register, non-volatile) for
            # delta calculations.  energy_integrated has better resolution
            # (~0.8 Wh vs 10 Wh) but resets to 0 on ESP32 reboot, silently
            # losing all accumulated energy.  The Modbus register survives
            # power cycles and is the reliable source of truth.
            energy_for_delta = reading.energy_active
            delta_kwh = 0.0
            if prev_energy is not None and energy_for_delta >= prev_energy:
                delta_kwh = energy_for_delta - prev_energy

            cur.execute("""
                INSERT INTO meter_readings
                    (meter_id, account_number, reading_time,
                     wh_reading, power_kw, community, source)
                VALUES (%s, %s, %s, %s, %s, %s, 'iot')
                ON CONFLICT DO NOTHING
            """, (
                meter_id, account, ts,
                reading.energy_active * 1000, _watts_to_kw(reading.power_active), community,
            ))

            hour_key = ts.strftime("%Y-%m-%d %H:00:00+00")
            if delta_kwh > 0:
                cur.execute("""
                    INSERT INTO hourly_consumption
                        (account_number, meter_id, reading_hour, kwh, community, source)
                    VALUES (%s, %s, %s, %s, %s, 'iot')
                    ON CONFLICT (meter_id, reading_hour) DO UPDATE
                        SET kwh = hourly_consumption.kwh + EXCLUDED.kwh
                """, (account, meter_id, hour_key, round(delta_kwh, 4), community))

            fw = (reading.firmware_version or "").strip()[:64] or None

            cur.execute("""
                INSERT INTO prototype_meter_state
                    (meter_id, account_number, last_energy_kwh,
                     last_relay_status, last_seen_at, last_synced_at, firmware_version)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (meter_id) DO UPDATE SET
                    account_number = EXCLUDED.account_number,
                    last_energy_kwh = EXCLUDED.last_energy_kwh,
                    last_relay_status = EXCLUDED.last_relay_status,
                    last_seen_at = EXCLUDED.last_seen_at,
                    last_synced_at = NOW(),
                    firmware_version = COALESCE(EXCLUDED.firmware_version, prototype_meter_state.firmware_version)
            """, (
                meter_id, account, energy_for_delta,
                reading.relay, ts, fw,
            ))

            conn.commit()

            logger.info(
                "Meter reading: %s energy=%.2f kWh delta=%.4f relay=%s fw=%s",
                meter_id, reading.energy_active, delta_kwh, reading.relay, fw or "-",
            )

            return {
                "status": "ok",
                "meter_id": meter_id,
                "account": account,
                "delta_kwh": round(delta_kwh, 4),
                "relay": reading.relay,
                "firmware_version_stored": fw,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Meter reading ingest failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Meter role management (check ↔ primary transitions)
# ---------------------------------------------------------------------------

VALID_ROLES = {"primary", "check", "backup"}


@router.get("/api/meters/account/{account_number}")
def get_meters_for_account(account_number: str):
    """List all meters for an account with their roles and platform."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT meter_id, platform, role, status FROM meters "
                "WHERE account_number = %s ORDER BY role, meter_id",
                (account_number,),
            )
            return [
                {"meter_id": r[0], "platform": r[1], "role": r[2], "status": r[3]}
                for r in cur.fetchall()
            ]
    except Exception as e:
        logger.error("Meter lookup failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class MeterRoleUpdate(BaseModel):
    meter_id: str
    role: str


@router.patch("/api/meters/{meter_id}/role")
def update_meter_role(meter_id: str, body: MeterRoleUpdate):
    """Change a meter's role. When promoting a check meter to primary on an
    account that already has a primary, the old primary is demoted to 'check'
    automatically — there can only be one primary per account."""
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}. Must be one of {VALID_ROLES}")

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            cur.execute(
                "SELECT account_number, role FROM meters WHERE meter_id = %s",
                (meter_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Meter not found: {meter_id}")

            account, old_role = row[0], row[1]

            if body.role == "primary" and old_role != "primary":
                cur.execute(
                    "UPDATE meters SET role = 'check', updated_at = NOW() "
                    "WHERE account_number = %s AND role = 'primary' AND meter_id != %s",
                    (account, meter_id),
                )
                demoted = cur.rowcount
            else:
                demoted = 0

            cur.execute(
                "UPDATE meters SET role = %s, updated_at = NOW() WHERE meter_id = %s",
                (body.role, meter_id),
            )
            conn.commit()

            logger.info("Meter %s role: %s → %s (account %s, demoted %d)",
                        meter_id, old_role, body.role, account, demoted)

            return {
                "meter_id": meter_id,
                "account": account,
                "old_role": old_role,
                "new_role": body.role,
                "demoted_count": demoted,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Meter role update failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# SMS payment ingestion (mirrored from sms.1pwrafrica.com/receive.php)
# ---------------------------------------------------------------------------
# Flow: SMS gateway → sms.1pwrafrica.com may mirror JSON to this endpoint.
# Account: Remark field (customer account) first; phone lookup as fallback.
# See mpesa_sms.parse_ls_sms_payment (M-Pesa + EcoCash) / resolve_sms_account.
# Format: {"messages": [{"id","from","content","sms_sent",...}]}


def _notify_sms_phone_fallback(
    account: str,
    amount: float,
    phone: str,
    remark: str,
    mpesa_receipt: str,
    reason: str,
    payment_provider: str = "mpesa",
) -> None:
    """WhatsApp Customer Care — only when phone lookup was used (country-specific bridge)."""
    sym = COUNTRY.currency_symbol
    if COUNTRY.code == "BN":
        header = (
            "MTN MoMo SMS: account was resolved by PHONE "
            "(no matching account token in 1PDB from SMS text).\n"
        )
        pay_line = f"Amount: {amount:,.0f} {sym} ({COUNTRY.currency})\n"
        ref_line = f"MoMo / payment ref: {mpesa_receipt or '?'}\n"
    elif (payment_provider or "").lower() == "ecocash":
        header = (
            "EcoCash SMS: account was resolved by PHONE "
            "(not a valid Remark account in 1PDB).\n"
        )
        pay_line = f"Amount: {sym}{amount:.2f}\n"
        ref_line = f"EcoCash / payment ref: {mpesa_receipt or '?'}\n"
    else:
        header = (
            "M-Pesa SMS: account was resolved by PHONE "
            "(not a valid Remark account in 1PDB).\n"
        )
        pay_line = f"Amount: {sym}{amount:.2f}\n"
        ref_line = f"M-Pesa receipt: {mpesa_receipt or '?'}\n"
    text = (
        header
        + f"Credited account: {account}\n"
        + pay_line
        + f"Payer phone (from SMS): {phone}\n"
        + f"Remark / reference text: {remark or '(empty)'}\n"
        + ref_line
        + f"Reason: {reason}"
    )
    notify_cc_bridge(
        {
            "source": "sms_allocation",
            "account_number": account,
            "category": "sms_phone_fallback",
            "text": text,
        },
        country_code=COUNTRY.code,
    )


def _sms_ingest_credit_sm(
    account_number: str,
    amount: float,
    txn_id: int,
    mpesa_receipt: str,
) -> None:
    """Background: credit Koios/ThunderCloud after SMS payment is in 1PDB."""
    memo = f"sms_incoming ref={mpesa_receipt or '?'} txn={txn_id}"
    try:
        result = credit_sparkmeter(
            account_number=account_number,
            amount=amount,
            memo=memo,
            external_id=str(txn_id),
        )
    except Exception as e:
        logger.error(
            "SMS path SM credit raised for %s txn=%s: %s",
            account_number, txn_id, e,
        )
        return
    if result.success:
        sym = COUNTRY.currency_symbol
        logger.info(
            "SMS path SM credit OK for %s %s%.2f → %s",
            account_number, sym, amount, result.platform,
        )
    else:
        logger.warning(
            "SMS path SM credit failed for %s (txn=%s): %s",
            account_number, txn_id, result.error,
        )


def _parse_gateway_payment(content: str, sender: str = ""):
    """M-Pesa + EcoCash (LS) or MTN MoMo (BN) depending on COUNTRY_CODE."""
    if COUNTRY.code == "BN":
        return parse_momo_bn_sms(content)
    return parse_ls_sms_payment(content, sender)


def _resolve_gateway_account(conn, content: str, parsed: dict) -> tuple:
    if COUNTRY.code == "BN":
        return resolve_bn_momo_account(conn, content, parsed)
    return resolve_sms_account(conn, content, parsed)


def _default_tariff_fallback() -> float:
    return COUNTRY.default_tariff_rate


@router.post("/api/sms/incoming")
@router.post("/api/bn/sms/incoming")
async def sms_incoming(request: Request, background_tasks: BackgroundTasks):
    """Receive mirrored SMS JSON from the national SMS gateway (PHP mirror).

    **Lesotho** (``COUNTRY_CODE=LS``): ``mpesa_sms.parse_ls_sms_payment`` (M-Pesa and EcoCash / short code 199).
    **Benin** (``COUNTRY_CODE=BN``): MTN MoMo templates via ``momo_bj`` (same JSON shape).

    **Flow (unchanged for EcoCash):** insert payment into **1PDB** first; only then (unless
    ``SMS_INGEST_PUSH_SPARKMETER=0``) background ``credit_sparkmeter`` — no direct Koios/PHP-only path.
    ``/api/bn/sms/incoming`` is an alias for operators routing ``smsbn.1pwrafrica.com`` behind ``/api/bn``.
    """
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("SMS incoming: invalid JSON")
        return {"messages": []}

    messages = payload.get("messages", [])
    if not messages:
        return {"messages": []}

    for msg in messages:
        msg_id = msg.get("id", "")
        content = msg.get("content", "")
        sender = msg.get("from", "")
        sms_received = msg.get("sms_received", 0) or msg.get("sms_sent", 0)

        logger.info("SMS from=%s id=%s content=%.60s…", sender, msg_id, content)

        parsed = _parse_gateway_payment(content, sender)
        if not parsed:
            if COUNTRY.code == "LS":
                logger.warning(
                    "SMS ingest: unparsed Lesotho message (no M-Pesa/EcoCash match) "
                    "from=%s id=%s snippet=%.220s",
                    sender,
                    msg_id,
                    content,
                )
            continue

        amount = parsed["amount"]
        phone = parsed["phone"]
        reference = parsed.get("reference") or ""
        receipt_key = (parsed.get("txn_id") or "").strip() or (reference or "").strip()

        try:
            with get_connection() as conn:
                if receipt_key and mpesa_receipt_in_use(conn, receipt_key):
                    logger.info(
                        "SMS duplicate payment ref %s — skipping",
                        receipt_key,
                    )
                    continue

                account, allocation, remark_stored, fb_reason = _resolve_gateway_account(
                    conn, content, parsed,
                )

                if not account:
                    if COUNTRY.code == "BN":
                        logger.warning(
                            "SMS payment %.0f %s from %s (ref %s) — no account (text/phone)",
                            amount, COUNTRY.currency, phone, reference,
                        )
                    else:
                        logger.warning(
                            "SMS payment M%.2f from %s (ref %s) — no account (remark/phone)",
                            amount, phone, reference,
                        )
                    continue

                cur = conn.cursor()

                cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
                rate_row = cur.fetchone()
                rate = float(rate_row[0]) if rate_row else _default_tariff_fallback()
                kwh = round(amount / rate, 4) if rate > 0 else 0.0

                cur.execute("""
                    SELECT COALESCE(
                        (SELECT current_balance FROM transactions
                         WHERE account_number = %s ORDER BY transaction_date DESC LIMIT 1),
                        0
                    )
                """, (account,))
                prev_balance = float(cur.fetchone()[0])
                new_balance = round(prev_balance + amount, 4)

                try:
                    ts_ms = int(sms_received)
                    if ts_ms > 0:
                        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    else:
                        ts = datetime.now(timezone.utc)
                except (ValueError, TypeError, OSError):
                    ts = datetime.now(timezone.utc)

                payer_phone = "".join(c for c in str(phone) if c.isdigit()) or str(phone)

                try:
                    cur.execute("""
                        INSERT INTO transactions
                            (account_number, meter_id, transaction_date,
                             transaction_amount, rate_used, kwh_value,
                             is_payment, current_balance, source,
                             payment_reference, sms_payer_phone, sms_remark_raw, sms_allocation)
                        VALUES (%s, '', %s, %s, %s, %s, true, %s, 'sms_gateway',
                                %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        account, ts, amount, rate, kwh, new_balance,
                        receipt_key or None,
                        payer_phone,
                        remark_stored or None,
                        allocation,
                    ))
                except psycopg2.IntegrityError:
                    conn.rollback()
                    logger.info("SMS skipped duplicate payment ref %s", receipt_key)
                    continue
                except Exception as e:
                    err = str(e).lower()
                    conn.rollback()
                    if (
                        "sms_payer_phone" in err
                        or "sms_remark_raw" in err
                        or "sms_allocation" in err
                    ) and "does not exist" in err:
                        cur.execute("""
                            INSERT INTO transactions
                                (account_number, meter_id, transaction_date,
                                 transaction_amount, rate_used, kwh_value,
                                 is_payment, current_balance, source,
                                 payment_reference)
                            VALUES (%s, '', %s, %s, %s, %s, true, %s, 'sms_gateway', %s)
                            RETURNING id
                        """, (
                            account, ts, amount, rate, kwh, new_balance,
                            receipt_key or None,
                        ))
                    elif "payment_reference" in err and "does not exist" in err:
                        cur.execute("""
                            INSERT INTO transactions
                                (account_number, meter_id, transaction_date,
                                 transaction_amount, rate_used, kwh_value,
                                 is_payment, current_balance, source)
                            VALUES (%s, '', %s, %s, %s, %s, true, %s, 'sms_gateway')
                            RETURNING id
                        """, (account, ts, amount, rate, kwh, new_balance))
                    else:
                        raise
                txn_db_id = cur.fetchone()[0]
                conn.commit()

                if COUNTRY.code == "BN":
                    logger.info(
                        "SMS payment (MoMo): txn=%d acct=%s alloc=%s %.0f %s from %s ref=%s receipt=%s",
                        txn_db_id, account, allocation, amount, COUNTRY.currency,
                        phone, reference, receipt_key,
                    )
                else:
                    prov = (parsed.get("provider") or "mpesa").lower()
                    logger.info(
                        "SMS payment (%s): txn=%d acct=%s alloc=%s M%.2f from %s ref=%s receipt=%s",
                        prov,
                        txn_db_id, account, allocation, amount, phone, reference,
                        receipt_key,
                    )

                if allocation == "phone_fallback":
                    background_tasks.add_task(
                        _notify_sms_phone_fallback,
                        account,
                        amount,
                        phone,
                        remark_stored or "",
                        receipt_key,
                        fb_reason,
                        (parsed.get("provider") or "mpesa"),
                    )

                if SMS_INGEST_PUSH_SPARKMETER and amount > 0:
                    background_tasks.add_task(
                        _sms_ingest_credit_sm,
                        account,
                        amount,
                        txn_db_id,
                        receipt_key or "",
                    )

                cur.execute(
                    "SELECT community FROM meters "
                    "WHERE account_number = %s AND platform = 'sparkmeter' LIMIT 1",
                    (account,),
                )
                comm_row = cur.fetchone()
                if comm_row and comm_row[0]:
                    background_tasks.add_task(sync_consumption_for_site, comm_row[0])

        except Exception as e:
            logger.error("SMS payment processing failed (forwarded OK): %s", e)

    # Return empty messages array — no outbound SMS for now
    return {"messages": []}


# ---------------------------------------------------------------------------
# Manual / external consumption sync trigger
# ---------------------------------------------------------------------------

@router.post("/api/sync/consumption/{community}")
def trigger_consumption_sync(community: str, background_tasks: BackgroundTasks):
    """Trigger a Koios consumption sync for a specific site.

    Call after crediting a meter or whenever fresh data is needed.
    Returns immediately; sync runs in background.
    """
    code = community.upper()
    if code not in KOIOS_SITES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown community: {community}. Valid: {list(KOIOS_SITES.keys())}",
        )
    background_tasks.add_task(sync_consumption_for_site, code)
    return {"status": "sync_queued", "community": code}
