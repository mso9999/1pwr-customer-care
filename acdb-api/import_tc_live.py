"""
Import real-time MAK consumption from ThunderCloud v0 API.

Strategy (v2 — cumulative register approach):
  1. Fetch all customers with reading_details=true from TC v0 API
  2. Store the raw cumulative energy register (total_cycle_energy) in
     meter_readings as wh_reading — this is non-lossy even if we miss a cycle
  3. Compute hourly consumption by differencing consecutive cumulative
     readings, then upsert into hourly_consumption
  4. If cumulative data is unavailable, fall back to latest_reading interval
     delta (v1 behavior)

This eliminates the permanent data loss that occurred when the previous
interval-only approach missed a 15-minute cycle.

Usage:
    python3 import_tc_live.py          # import latest readings
    python3 import_tc_live.py --dry-run # preview only
"""
import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
import requests
import warnings

warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tc_live")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api:gKkYLkzYwSRPNoSwuC87YVqbzCmnhI4e@localhost:5432/onepower_cc",
)
TC_BASE = os.environ.get(
    "TC_API_BASE",
    "https://sparkcloud-u740425.sparkmeter.cloud",
)
TC_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
COMMUNITY = "MAK"
SOURCE = "thundercloud"


def full_serial_to_short(serial: str) -> str:
    """Convert a full SM serial (e.g. SMRSD-03-0002E040) to the short numeric
    code used by the parquet importer (e.g. 57408).

    The short code is the last 4 hex digits of the serial's hex portion,
    interpreted as an unsigned 16-bit integer in decimal.
    """
    parts = serial.split("-")
    hex_part = parts[-1] if len(parts) >= 3 else ""
    if len(hex_part) >= 4:
        try:
            return str(int(hex_part[-4:], 16))
        except ValueError:
            pass
    return serial


def fetch_tc_customers():
    """Fetch all TC customers with full meter + reading details."""
    headers = {"Authentication-Token": TC_TOKEN}
    r = requests.get(
        f"{TC_BASE}/api/v0/customers?customers_only=true&reading_details=true",
        headers=headers, timeout=120, verify=False,
    )
    if r.status_code == 401:
        log.error("401 Unauthorized — TC_AUTH_TOKEN is stale, regenerate from SparkCloud dashboard")
        sys.exit(1)
    r.raise_for_status()
    return r.json().get("customers", [])


def store_cumulative_readings(cur, customers):
    """Store cumulative energy register values in meter_readings.
    Returns list of (serial, account, timestamp, cumulative_kwh) for delta computation."""
    readings = []
    for cust in customers:
        code = cust.get("code", "")
        if not code:
            continue
        for meter in cust.get("meters", []):
            serial = meter.get("serial", "")
            if not serial:
                continue

            lr = meter.get("latest_reading")
            if not lr:
                continue
            ts_str = lr.get("timestamp")
            if not ts_str:
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            total_energy = meter.get("total_cycle_energy")
            daily_energy = meter.get("current_daily_energy")
            interval_kwh = lr.get("kilowatt_hours", 0)
            power_kw = lr.get("avg_true_power", 0) or 0

            cumulative_wh = None
            if total_energy is not None:
                try:
                    cumulative_wh = float(total_energy) * 1000.0
                except (ValueError, TypeError):
                    pass

            readings.append({
                "serial": serial,
                "account": code,
                "timestamp": dt,
                "cumulative_wh": cumulative_wh,
                "interval_kwh": float(interval_kwh) if interval_kwh else 0.0,
                "power_kw": float(power_kw) if power_kw else 0.0,
                "daily_energy": float(daily_energy) if daily_energy is not None else None,
            })

    if not readings:
        return []

    batch = []
    for r in readings:
        batch.append((
            r["serial"], r["account"], r["timestamp"],
            r["cumulative_wh"], r["power_kw"],
            COMMUNITY, SOURCE,
        ))
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO meter_readings
            (meter_id, account_number, reading_time, wh_reading, power_kw,
             community, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meter_id, reading_time) DO NOTHING
    """, batch, page_size=200)

    return readings


def compute_hourly_from_cumulative(cur, readings):
    """Compute hourly consumption by differencing consecutive cumulative register
    values from meter_readings. Returns rows for hourly_consumption upsert."""
    serials = list(set(r["serial"] for r in readings if r["cumulative_wh"] is not None))
    if not serials:
        return []

    lookback = datetime.now(timezone.utc) - timedelta(hours=26)
    cur.execute("""
        SELECT meter_id, reading_time, wh_reading, account_number
        FROM meter_readings
        WHERE meter_id = ANY(%s)
          AND reading_time >= %s
          AND source = 'thundercloud'
          AND wh_reading IS NOT NULL
        ORDER BY meter_id, reading_time
    """, (serials, lookback))

    rows_by_meter = defaultdict(list)
    acct_by_meter = {}
    for meter_id, reading_time, wh_reading, account_number in cur.fetchall():
        rows_by_meter[meter_id].append((reading_time, float(wh_reading)))
        if account_number:
            acct_by_meter[meter_id] = account_number

    hourly_rows = []
    for serial, time_series in rows_by_meter.items():
        if len(time_series) < 2:
            continue
        acct = acct_by_meter.get(serial, serial)

        hourly_accum = defaultdict(float)
        for i in range(1, len(time_series)):
            t_prev, wh_prev = time_series[i - 1]
            t_curr, wh_curr = time_series[i]
            delta_wh = wh_curr - wh_prev
            if delta_wh < 0 or delta_wh > 50000:
                continue
            delta_kwh = delta_wh / 1000.0

            hour_key = t_curr.replace(minute=0, second=0, microsecond=0)
            hourly_accum[hour_key] += delta_kwh

        short = full_serial_to_short(serial)
        for hour_ts, kwh in hourly_accum.items():
            if kwh > 0:
                hourly_rows.append((acct, short, hour_ts, round(kwh, 4), COMMUNITY, SOURCE))

    return hourly_rows


def fallback_interval_rows(readings):
    """Fall back to interval-based rows for meters without cumulative data.
    Aggregates to hourly buckets to stay consistent with the table schema."""
    hourly_accum = defaultdict(float)
    acct_by_serial = {}
    for r in readings:
        if r["cumulative_wh"] is not None:
            continue
        kwh = r["interval_kwh"]
        if kwh is None or kwh <= 0:
            continue
        short = full_serial_to_short(r["serial"])
        hour_key = r["timestamp"].replace(minute=0, second=0, microsecond=0)
        hourly_accum[(short, hour_key)] += kwh
        acct_by_serial[short] = r["account"]

    rows = []
    for (short, hour_ts), kwh in hourly_accum.items():
        acct = acct_by_serial.get(short, short)
        rows.append((acct, short, hour_ts, round(kwh, 4), COMMUNITY, SOURCE))
    return rows


def main():
    parser = argparse.ArgumentParser(description="Import live MAK readings from ThunderCloud v0 API")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not TC_TOKEN:
        log.error("TC_AUTH_TOKEN not set — cannot query v0 API")
        sys.exit(1)

    log.info("Fetching live readings from ThunderCloud v0 API...")
    customers = fetch_tc_customers()
    log.info("Got %d customers", len(customers))

    if not customers:
        log.info("No customers returned.")
        return

    if args.dry_run:
        cumul_count = sum(
            1 for c in customers for m in c.get("meters", [])
            if m.get("total_cycle_energy") is not None
        )
        interval_count = sum(
            1 for c in customers for m in c.get("meters", [])
            if m.get("latest_reading", {}).get("kilowatt_hours")
        )
        log.info("DRY RUN — %d meters with cumulative register, %d with interval readings",
                 cumul_count, interval_count)
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    log.info("Step 1: Storing cumulative readings in meter_readings...")
    readings = store_cumulative_readings(cur, customers)
    conn.commit()
    cumul_count = sum(1 for r in readings if r["cumulative_wh"] is not None)
    log.info("  Stored %d readings (%d with cumulative register)", len(readings), cumul_count)

    log.info("Step 2: Computing hourly consumption from cumulative register diffs...")
    hourly_rows = compute_hourly_from_cumulative(cur, readings)
    log.info("  Computed %d hourly rows from cumulative diffs", len(hourly_rows))

    log.info("Step 3: Fallback interval rows for meters without cumulative data...")
    fallback_rows = fallback_interval_rows(readings)
    log.info("  %d fallback interval rows", len(fallback_rows))

    all_rows = hourly_rows + fallback_rows
    if all_rows:
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO hourly_consumption
                (account_number, meter_id, reading_hour, kwh, community, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (meter_id, reading_hour)
            DO UPDATE SET kwh = GREATEST(hourly_consumption.kwh, EXCLUDED.kwh)
        """, all_rows, page_size=200)
        inserted = cur.rowcount
        conn.commit()
        log.info("Upserted %d / %d hourly consumption rows", inserted, len(all_rows))
    else:
        log.info("No consumption rows to insert.")

    conn.close()
    log.info("DONE.")


if __name__ == "__main__":
    main()
