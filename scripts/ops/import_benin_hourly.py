#!/usr/bin/env python3
"""
Import Benin hourly consumption from Koios daily-granularity API.

Fetches 15-minute heartbeat readings, aggregates to hourly buckets,
and upserts into onepower_bj.hourly_consumption.

Usage:
    python3 import_benin_hourly.py                        # last 2 days
    python3 import_benin_hourly.py 2026-03-01 2026-03-31  # date range
    python3 import_benin_hourly.py --dry-run               # preview only
"""

import csv
import io
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("import-bj-hourly")

DB_URL = os.environ["DATABASE_URL"]
API_KEY = os.environ["KOIOS_API_KEY"]
API_SECRET = os.environ["KOIOS_API_SECRET"]
BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
HEADERS = {"X-API-KEY": API_KEY, "X-API-SECRET": API_SECRET}

SITES = {
    "GBO": "a23c334e-33f7-473d-9ae3-9e631d5336e4",
    "SAM": "8f80b0a8-0502-4e26-9043-7152979360aa",
}

SOURCE = "koios"


def fetch_daily_readings(site_id: str, date_str: str) -> str | None:
    url = f"{BASE}/api/v2/report"
    params = {
        "granularity": "daily",
        "type": "readings",
        "site_id": site_id,
        "date": date_str,
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=120)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def parse_to_hourly(raw_csv: str, site_code: str) -> dict:
    """Parse daily CSV into hourly buckets: {(account, hour_str): kwh}."""
    reader = csv.DictReader(io.StringIO(raw_csv))
    hourly = defaultdict(float)

    for row in reader:
        acct = (row.get("meter/customer/code") or "").strip()
        if not acct or acct == "None":
            continue

        kwh_str = row.get("kilowatt_hours", "0")
        try:
            kwh = float(kwh_str or 0)
        except (ValueError, TypeError):
            continue
        if kwh <= 0:
            continue

        hb_start = row.get("heartbeat_start", "").strip()
        if not hb_start:
            continue

        try:
            ts = datetime.strptime(hb_start[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        hour_str = ts.strftime("%Y-%m-%d %H:00:00")
        hourly[(acct, hour_str)] += kwh

    return hourly


def import_day(conn, date_str: str, dry_run: bool = False) -> int:
    """Import one day's hourly data for all sites. Returns record count."""
    all_hourly = {}

    for site_code, site_id in SITES.items():
        raw = fetch_daily_readings(site_id, date_str)
        if raw is None:
            log.info("  %s %s: no data", site_code, date_str)
            continue

        hourly = parse_to_hourly(raw, site_code)
        log.info("  %s %s: %d hourly buckets from API", site_code, date_str, len(hourly))
        all_hourly.update(hourly)
        time.sleep(0.3)

    if not all_hourly:
        return 0

    batch = []
    for (acct, hour_str), kwh in all_hourly.items():
        community = ""
        alpha = "".join(c for c in acct if c.isalpha()).upper()
        if "SAM" in alpha:
            community = "SAM"
        elif "GBO" in alpha:
            community = "GBO"
        batch.append((acct, acct, hour_str, round(kwh, 6), community, SOURCE))

    if dry_run:
        log.info("  DRY RUN: would upsert %d hourly records for %s", len(batch), date_str)
        return len(batch)

    cur = conn.cursor()
    CHUNK = 2000
    for i in range(0, len(batch), CHUNK):
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO hourly_consumption
                (account_number, meter_id, reading_hour, kwh, community, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (meter_id, reading_hour)
                DO UPDATE SET kwh = EXCLUDED.kwh
            """,
            batch[i:i + CHUNK],
            page_size=500,
        )
    conn.commit()
    return len(batch)


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d")
        end = datetime.strptime(args[1], "%Y-%m-%d")
    elif len(args) == 1:
        start = datetime.strptime(args[0], "%Y-%m-%d")
        end = start
    else:
        end = datetime.utcnow()
        start = end - timedelta(days=1)

    conn = None
    if not dry_run:
        conn = psycopg2.connect(DB_URL)

    total = 0
    d = start
    while d <= end:
        date_str = d.strftime("%Y-%m-%d")
        log.info("Processing %s...", date_str)
        n = import_day(conn, date_str, dry_run=dry_run)
        total += n
        d += timedelta(days=1)

    log.info("Total: %d hourly records across %d day(s)", total, (end - start).days + 1)

    if conn:
        try:
            cur = conn.cursor()
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_hourly_account_summary")
            conn.commit()
            log.info("Refreshed mv_hourly_account_summary")
        except Exception as e:
            log.warning("Could not refresh matview: %s", e)
        conn.close()


if __name__ == "__main__":
    main()
