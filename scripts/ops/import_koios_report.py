#!/usr/bin/env python3
"""Import Koios hourly consumption via the /api/v2/report endpoint.

Why this exists
---------------
The legacy LS importer (``import_hourly.py``) reads Koios v2 ``data/historical``,
which has degraded to **daily aggregates** ("N meters but only 1 hour") for any
date older than ~1-2 days. When the daily sync misses a day, that endpoint can
no longer supply hourly data and the importer skips it -> a permanent hourly hole
-> CC under-counts consumption -> the customer balance drifts above SparkMeter.

``GET /api/v2/report`` (``granularity=daily&type=readings``, same X-API-KEY/SECRET)
still returns full **15-minute heartbeats** (``heartbeat_start`` in UTC,
``kilowatt_hours`` per period) for historical dates. This is the same source BN's
``import_benin_hourly.py`` already uses. Verified: ``heartbeat_start`` hour aligns
exactly with the existing UTC ``reading_hour`` (no timezone shift).

This script fetches that report, bins 15-min -> hourly, and upserts into
``hourly_consumption``. The balance engine and the monthly aggregate both
de-duplicate by (account, reading_hour) via MAX(kwh), so even if a serial/meter_id
differs from a pre-existing row the balance is never double-counted.

Usage
-----
    DATABASE_URL=... KOIOS_API_KEY=... KOIOS_API_SECRET=... \
      python3 import_koios_report.py 2026-03-07 2026-06-05 --country LS --apply

    # single site, dry run (no writes):
    python3 import_koios_report.py 2026-03-07 2026-06-05 --country LS --site MAS
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")

# Sanity cap on a single 15-min heartbeat's kWh. Koios occasionally emits a
# garbage reading (e.g. a cumulative register / sentinel leaking into the
# interval field — observed 1.66e12 kWh for one LS meter-hour on 2026-02-26),
# which would otherwise over-debit a balance into oblivion. A real sub-hourly
# single-meter reading is well under this; anything above is dropped.
# (RCA 2026-06-17.)
MAX_READING_KWH = float(os.environ.get("MAX_READING_KWH", "100"))

# Site UUID maps mirror import_hourly.py ORGS. Keys read from env per country.
ORGS = {
    "LS": {
        "api_key_env": ("KOIOS_API_KEY",),
        "api_secret_env": ("KOIOS_API_SECRET",),
        "sites": {
            "MAT": "2f7c38b8-4a70-44fd-bf9c-ebf2b2aa78c0",
            "TLH": "db5bf699-31ea-44b6-91c5-1b41e4a2d130",
            "MAS": "101c443e-6500-4a4d-8cdc-6bd15f4388c8",
            "SHG": "bd7c477d-0742-4056-b75c-38b14ac7cf97",
            "KET": "a075cbc1-e920-455e-9d5a-8595061dfec0",
            "LSB": "ed0766c4-9270-4254-a107-eb4464a96ed9",
            "SEH": "0a4fdca5-2d78-4979-8051-10f21a216b16",
            "TOS": "b564c8d6-a6c1-43d4-98d1-87ed8cd8ffd7",
        },
    },
    "BN": {
        "api_key_env": ("KOIOS_API_KEY_BN", "KOIOS_WRITE_API_KEY_BN"),
        "api_secret_env": ("KOIOS_API_SECRET_BN", "KOIOS_WRITE_API_SECRET_BN"),
        "sites": {
            "GBO": "a23c334e-33f7-473d-9ae3-9e631d5336e4",
            "SAM": "8f80b0a8-0502-4e26-9043-7152979360aa",
        },
    },
}


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def _resolve_keys(country: str) -> tuple[str, str]:
    cfg = ORGS[country]
    key = next((os.environ[e] for e in cfg["api_key_env"] if os.environ.get(e)), "")
    secret = next((os.environ[e] for e in cfg["api_secret_env"] if os.environ.get(e)), "")
    if not key or not secret:
        raise SystemExit(f"Missing Koios API key/secret for {country}")
    return key, secret


def fetch_report(site_id: str, date_str: str, key: str, secret: str) -> str | None:
    r = requests.get(
        f"{KOIOS_BASE}/api/v2/report",
        headers={"X-API-KEY": key, "X-API-SECRET": secret},
        params={
            "granularity": "daily",
            "type": "readings",
            "site_id": site_id,
            "date": date_str,
        },
        timeout=180,
    )
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise RuntimeError("429 rate limit")
    r.raise_for_status()
    return r.text


def parse_hourly(raw_csv: str) -> dict[tuple[str, str, str], float]:
    """Return {(account, meter_serial, 'YYYY-MM-DD HH:00:00'): kwh} from 15-min rows.

    Koios's /api/v2/report intermittently returns each 15-minute heartbeat
    duplicated N times (observed up to ~11x for Lesotho in Jan 2026). Summing
    every CSV row inflated hourly kWh by the duplication factor, over-debiting
    balances. We collapse identical heartbeats by (serial, heartbeat_start) —
    duplicates carry byte-identical kWh — before binning to the hour.
    (RCA 2026-06-17.)
    """
    # (serial, heartbeat_start) -> (acct, hour_key, kwh) for each DISTINCT heartbeat.
    heartbeats: dict[tuple[str, str], tuple[str, str, float]] = {}
    for row in csv.DictReader(io.StringIO(raw_csv)):
        acct = (row.get("meter/customer/code") or "").strip()
        if not acct or acct == "None":
            continue
        serial = (row.get("meter/serial") or "").strip()
        hb = (row.get("heartbeat_start") or "").strip()
        if not serial or len(hb) < 13:
            continue
        try:
            kwh = float(row.get("kilowatt_hours") or 0)
        except (ValueError, TypeError):
            continue
        if kwh > MAX_READING_KWH:
            continue  # garbage reading (see MAX_READING_KWH note)
        hour_key = hb[:13] + ":00:00"  # heartbeat_start is UTC; truncate to hour
        heartbeats[(serial, hb)] = (acct, hour_key, kwh)

    out: dict[tuple[str, str, str], float] = defaultdict(float)
    for (serial, hb), (acct, hour_key, kwh) in heartbeats.items():
        out[(acct, serial, hour_key)] += kwh
    return out


def upsert(conn, community: str, hourly: dict, apply: bool) -> int:
    if not hourly:
        return 0
    # The unique conflict key is (meter_id, reading_hour). If a meter_serial
    # appears under more than one account in the same hour (mid-day reassignment
    # / Koios data quirk), the batch would contain duplicate constrained values
    # and ON CONFLICT DO UPDATE errors ("cannot affect row a second time").
    # Collapse to one row per (serial, hour): sum total kWh on that meter-hour
    # and attribute it to the account with the largest share.
    agg: dict[tuple[str, str], dict] = {}
    for (acct, serial, hour), kwh in hourly.items():
        slot = agg.setdefault((serial, hour), {"kwh": 0.0, "by_acct": defaultdict(float)})
        slot["kwh"] += kwh
        slot["by_acct"][acct] += kwh
    rows = []
    for (serial, hour), slot in agg.items():
        acct = max(slot["by_acct"].items(), key=lambda kv: kv[1])[0]
        rows.append((acct, serial, hour, round(slot["kwh"], 6), community))
    if not apply:
        return len(rows)
    cur = conn.cursor()
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO hourly_consumption
            (account_number, meter_id, reading_hour, kwh, community, source)
        VALUES %s
        ON CONFLICT (meter_id, reading_hour)
        DO UPDATE SET kwh = EXCLUDED.kwh, community = EXCLUDED.community
        """,
        rows,
        template="(%s,%s,%s,%s,%s,'koios'::transaction_source)",
        page_size=1000,
    )
    conn.commit()
    return len(rows)


def daterange(start: datetime, end: datetime):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("from_date")
    ap.add_argument("to_date", nargs="?", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--country", default="LS")
    ap.add_argument("--site", help="Single site code (else all sites for country)")
    ap.add_argument("--apply", action="store_true", help="Write to DB (default: dry run)")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds between API calls")
    args = ap.parse_args()

    country = args.country.upper()
    if country not in ORGS:
        raise SystemExit(f"Unknown country {country}")
    key, secret = _resolve_keys(country)

    sites = ORGS[country]["sites"]
    if args.site:
        sites = {args.site.upper(): sites[args.site.upper()]}

    db = os.environ.get("DATABASE_URL")
    if not db:
        raise SystemExit("DATABASE_URL required")
    conn = psycopg2.connect(db)

    start = datetime.strptime(args.from_date, "%Y-%m-%d")
    end = datetime.strptime(args.to_date, "%Y-%m-%d")
    grand = 0
    for site_code, site_id in sites.items():
        site_total = 0
        for d in daterange(start, end):
            ds = d.strftime("%Y-%m-%d")
            try:
                raw = fetch_report(site_id, ds, key, secret)
            except RuntimeError as exc:
                _log(f"  {site_code} {ds}: {exc} - stopping site")
                break
            time.sleep(args.delay)
            if not raw:
                continue
            hourly = parse_hourly(raw)
            n = upsert(conn, site_code, hourly, args.apply)
            site_total += n
        _log(f"{site_code}: {site_total} hourly rows {'upserted' if args.apply else '(dry-run)'}")
        grand += site_total
    _log(f"GRAND TOTAL: {grand} hourly rows ({'APPLIED' if args.apply else 'DRY-RUN'})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
