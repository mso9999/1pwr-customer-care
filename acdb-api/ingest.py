"""
Real-time data ingestion endpoints for 1PDB.

Provides:
  - POST /api/meters/reading    — receive prototype meter readings from ingestion_gate Lambda
  - POST /api/sms/incoming      — receive mirrored SMS from sms.1pwrafrica.com, parse M-PESA

The ingestion_gate Lambda (IoT Core → Lambda) forwards each reading here.
The SMS Gateway app (Medic Mobile fork) on the merchant phone sends SMS to
sms.1pwrafrica.com/receive.php (SMSComms repo), which mirrors payloads here
after its own processing — 1PDB is a passive secondary receiver.
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

PROTOTYPE_METERS = {
    "23022673": "0045MAK",
    "23022628": "0005MAK",
    "23022696": "0025MAK",
}


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

    account = PROTOTYPE_METERS.get(reading.meter_id)
    if not account:
        raise HTTPException(status_code=404, detail=f"Unknown prototype meter: {reading.meter_id}")

    try:
        ts = datetime.strptime(reading.timestamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Bad timestamp format: {reading.timestamp}")

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Get previous energy for delta calculation
            cur.execute(
                "SELECT last_energy_kwh FROM prototype_meter_state WHERE meter_id = %s",
                (reading.meter_id,),
            )
            row = cur.fetchone()
            prev_energy = float(row[0]) if row else None

            delta_kwh = 0.0
            if prev_energy is not None and reading.energy_active >= prev_energy:
                delta_kwh = reading.energy_active - prev_energy

            # Insert raw reading
            cur.execute("""
                INSERT INTO meter_readings
                    (meter_id, account_number, reading_time,
                     wh_reading, power_kw, community, source)
                VALUES (%s, %s, %s, %s, %s, 'MAK', 'iot')
                ON CONFLICT DO NOTHING
            """, (
                reading.meter_id, account, ts,
                reading.energy_active * 1000, reading.power_active,
            ))

            # Hourly bin
            hour_key = ts.strftime("%Y-%m-%d %H:00:00+00")
            if delta_kwh > 0:
                cur.execute("""
                    INSERT INTO hourly_consumption
                        (account_number, meter_id, reading_hour, kwh, community, source)
                    VALUES (%s, %s, %s, %s, 'MAK', 'iot')
                    ON CONFLICT (meter_id, reading_hour) DO UPDATE
                        SET kwh = hourly_consumption.kwh + EXCLUDED.kwh
                """, (account, reading.meter_id, hour_key, round(delta_kwh, 4)))

            # Update state
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
                reading.meter_id, account, reading.energy_active,
                reading.relay, ts,
            ))

            conn.commit()

            logger.info("Meter reading: %s energy=%.2f kWh delta=%.4f relay=%s",
                        reading.meter_id, reading.energy_active, delta_kwh, reading.relay)

            return {
                "status": "ok",
                "meter_id": reading.meter_id,
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
        sms_sent = msg.get("sms_sent", 0)

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
                    ts = datetime.fromtimestamp(int(sms_sent) / 1000, tz=timezone.utc)
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
