"""
Import real-time MAK consumption from ThunderCloud v0 API.

Uses GET /api/v0/customers?customers_only=true&reading_details=true
to pull the latest 15-minute meter reading for all MAK meters and
insert into hourly_consumption. Designed to run every 15 minutes
via sync_consumption.sh to provide same-day data that parquet files
cannot (parquet is published daily, ~1 day lag).

Usage:
    python3 import_tc_live.py          # import latest readings
    python3 import_tc_live.py --dry-run # preview only
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

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


def main():
    parser = argparse.ArgumentParser(description="Import live MAK readings from ThunderCloud v0 API")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not TC_TOKEN:
        log.error("TC_AUTH_TOKEN not set — cannot query v0 API")
        sys.exit(1)

    log.info("Fetching live readings from ThunderCloud v0 API...")
    headers = {"Authentication-Token": TC_TOKEN}
    r = requests.get(
        f"{TC_BASE}/api/v0/customers?customers_only=true&reading_details=true",
        headers=headers, timeout=120, verify=False,
    )
    if r.status_code == 401:
        log.error("401 Unauthorized — TC_AUTH_TOKEN is stale, regenerate from SparkCloud dashboard")
        sys.exit(1)
    r.raise_for_status()

    data = r.json()
    customers = data.get("customers", [])
    log.info("Got %d customers", len(customers))

    rows = []
    for cust in customers:
        code = cust.get("code", "")
        if not code:
            continue
        for meter in cust.get("meters", []):
            lr = meter.get("latest_reading")
            if not lr:
                continue
            ts_str = lr.get("timestamp")
            kwh = lr.get("kilowatt_hours", 0)
            if not ts_str or kwh is None:
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            serial = meter.get("serial", "")
            rows.append((code, serial, dt, float(kwh), COMMUNITY, SOURCE))

    log.info("Readings to insert: %d", len(rows))
    if not rows:
        log.info("Nothing to import.")
        return

    if args.dry_run:
        for r in rows[:5]:
            log.info("  %s %s %s %.4f kWh", r[0], r[1], r[2], r[3])
        log.info("  ... (%d total, dry-run)", len(rows))
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO hourly_consumption
            (account_number, meter_id, reading_hour, kwh, community, source)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (meter_id, reading_hour) DO NOTHING
    """, rows, page_size=200)
    inserted = cur.rowcount
    conn.commit()
    conn.close()
    log.info("Inserted %d / %d readings (rest were duplicates)", inserted, len(rows))


if __name__ == "__main__":
    main()
