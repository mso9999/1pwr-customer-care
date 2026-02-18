"""
Real-time data ingestion and meter management endpoints for 1PDB.

Provides:
  - POST  /api/meters/reading                — prototype meter readings (from ingestion_gate Lambda)
  - GET   /api/meters/account/{account}      — list meters + roles for an account
  - PATCH /api/meters/{meter_id}/role        — change meter role (primary/check/backup)
  - POST  /api/sms/incoming                  — mirrored SMS from sms.1pwrafrica.com

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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from customer_api import get_connection

logger = logging.getLogger("cc-api.ingest")

router = APIRouter(tags=["ingest"])

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
    power_active: float = 0
    voltage: float = 0
    current: float = 0
    relay: str = "0"
    frequency: float = 0


@router.post("/api/meters/reading")
def ingest_meter_reading(reading: MeterReading, x_iot_key: str = Header(None)):
    if x_iot_key != IOT_KEY:
        raise HTTPException(status_code=403, detail="Invalid IoT key")

    try:
        ts = datetime.strptime(reading.timestamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
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

            delta_kwh = 0.0
            if prev_energy is not None and reading.energy_active >= prev_energy:
                delta_kwh = reading.energy_active - prev_energy

            cur.execute("""
                INSERT INTO meter_readings
                    (meter_id, account_number, reading_time,
                     wh_reading, power_kw, community, source)
                VALUES (%s, %s, %s, %s, %s, %s, 'iot')
                ON CONFLICT DO NOTHING
            """, (
                meter_id, account, ts,
                reading.energy_active * 1000, reading.power_active, community,
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

            cur.execute("""
                INSERT INTO prototype_meter_state
                    (meter_id, account_number, last_energy_kwh,
                     last_relay_status, last_seen_at, last_synced_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (meter_id) DO UPDATE SET
                    last_energy_kwh = EXCLUDED.last_energy_kwh,
                    last_relay_status = EXCLUDED.last_relay_status,
                    last_seen_at = EXCLUDED.last_seen_at,
                    last_synced_at = NOW()
            """, (
                meter_id, account, reading.energy_active,
                reading.relay, ts,
            ))

            conn.commit()

            logger.info("Meter reading: %s energy=%.2f kWh delta=%.4f relay=%s",
                        meter_id, reading.energy_active, delta_kwh, reading.relay)

            return {
                "status": "ok",
                "meter_id": meter_id,
                "account": account,
                "delta_kwh": round(delta_kwh, 4),
                "relay": reading.relay,
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
# The existing payment pipeline:
#   Merchant phone (SMS Gateway app) → sms.1pwrafrica.com/receive.php
#   → file drop to ./incoming/mpesa/ → new_file_watcher.php → SparkMeter API
#
# receive.php mirrors the raw JSON payload to this endpoint after its own
# processing.  Format: {"messages": [{"id","from","content","sms_sent",...}]}
#
# M-PESA Lesotho confirmation format:
#   "5L956Z39DJ Confirmed. on 9/12/18 at 8:59 AM M1.00 received from
#    26657755403 - Tamer Teker 26657755403.New M-Pesa balance is M387.80
#    Reference: 315103084."

MPESA_PATTERN = re.compile(
    r"(?P<txn_id>\w+)\s+Confirmed\.\s+on\s+.+?"
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+"
    r"(?P<phone>\d{8,15})"
    r".*?Reference:\s*(?P<ref>\d+)",
    re.IGNORECASE | re.DOTALL,
)

MPESA_FALLBACK = re.compile(
    r"M(?P<amount>\d+(?:\.\d{1,2})?)\s+received\s+from\s+(?P<phone>\d{8,15})",
    re.IGNORECASE,
)

REF_PATTERN = re.compile(r"Reference:\s*(\d+)", re.IGNORECASE)


def _parse_mpesa_sms(content: str) -> Optional[dict]:
    """Parse an M-PESA confirmation SMS. Returns dict or None."""
    m = MPESA_PATTERN.search(content)
    if m:
        return {
            "txn_id": m.group("txn_id"),
            "amount": float(m.group("amount")),
            "phone": m.group("phone"),
            "reference": m.group("ref"),
            "provider": "mpesa",
        }

    m = MPESA_FALLBACK.search(content)
    if m:
        ref_match = REF_PATTERN.search(content)
        return {
            "txn_id": "",
            "amount": float(m.group("amount")),
            "phone": m.group("phone"),
            "reference": ref_match.group(1) if ref_match else "",
            "provider": "mpesa",
        }

    return None


def _phone_to_account(conn, phone_digits: str) -> Optional[str]:
    """Look up account number from phone number."""
    normalized = phone_digits.lstrip("0")
    if normalized.startswith("266"):
        normalized = normalized[3:]

    cur = conn.cursor()
    cur.execute("""
        SELECT a.account_number
        FROM customers c
        JOIN accounts a ON a.customer_id = c.id
        WHERE replace(replace(replace(COALESCE(c.phone,''), '+', ''), ' ', ''), '-', '') LIKE %s
           OR replace(replace(replace(COALESCE(c.cell_phone_1,''), '+', ''), ' ', ''), '-', '') LIKE %s
        LIMIT 1
    """, (f"%{normalized}", f"%{normalized}"))
    row = cur.fetchone()
    return row[0] if row else None


@router.post("/api/sms/incoming")
async def sms_incoming(request: Request):
    """Receive mirrored SMS payload from the PHP at sms.1pwrafrica.com.

    The PHP (SMSComms/receive.php) does its own processing first (file drop,
    SparkMeter crediting) and then mirrors the raw Medic Mobile Gateway JSON
    here as a fire-and-forget POST.  We parse M-PESA payments and record them.
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

        parsed = _parse_mpesa_sms(content)
        if not parsed:
            continue

        amount = parsed["amount"]
        phone = parsed["phone"]
        reference = parsed["reference"]
        mpesa_txn_id = parsed["txn_id"]

        try:
            with get_connection() as conn:
                account = _phone_to_account(conn, phone)

                if not account:
                    logger.warning(
                        "SMS payment M%.2f from %s (ref %s) — no matching account",
                        amount, phone, reference,
                    )
                    continue

                cur = conn.cursor()

                cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate'")
                rate_row = cur.fetchone()
                rate = float(rate_row[0]) if rate_row else 5.0
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

                cur.execute("""
                    INSERT INTO transactions
                        (account_number, meter_id, transaction_date,
                         transaction_amount, rate_used, kwh_value,
                         is_payment, current_balance, source)
                    VALUES (%s, '', %s, %s, %s, %s, true, %s, 'sms_gateway')
                    RETURNING id
                """, (account, ts, amount, rate, kwh, new_balance))
                txn_db_id = cur.fetchone()[0]
                conn.commit()

                logger.info(
                    "SMS payment: txn=%d acct=%s M%.2f from %s ref=%s mpesa=%s",
                    txn_db_id, account, amount, phone, reference, mpesa_txn_id,
                )

        except Exception as e:
            logger.error("SMS payment processing failed (forwarded OK): %s", e)

    # Return empty messages array — no outbound SMS for now
    return {"messages": []}
