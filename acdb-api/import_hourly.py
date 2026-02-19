"""
Import hourly consumption from Koios v2 historical API into 1PDB.

Resilient design:
  - Single-day queries to avoid API 500s on multi-day ranges
  - Adaptive per_page (starts high, falls back automatically)
  - Retries with exponential backoff for transient 504/500 errors
  - Concurrent site fetching (configurable parallelism)
  - Staleness-aware: checks DB for latest data and only fetches gaps

Usage:
    python3 import_hourly.py                          # yesterday + today
    python3 import_hourly.py 2026-02-10               # from date to today
    python3 import_hourly.py 2026-02-10 2026-02-15    # specific range
    python3 import_hourly.py --site KET               # single site
    python3 import_hourly.py --workers 3              # parallel sites
"""
import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hourly")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api:gKkYLkzYwSRPNoSwuC87YVqbzCmnhI4e@localhost:5432/onepower_cc",
)
KOIOS_BASE = "https://www.sparkmeter.cloud"

ORGS = {
    "LS": {
        "org_id": "1cddcb07-6647-40aa-aaaa-70d762922029",
        "api_key": os.environ.get(
            "KOIOS_API_KEY",
            "SGWcnZpgCj-R0fGoVRtjbwMcElV7BvZGz00EEmJDv54",
        ),
        "api_secret": os.environ.get(
            "KOIOS_API_SECRET",
            "gJ5gHPsw21W8Jwl&!aId9O5uoywpg#2G",
        ),
        "sites": {
            "MAT": ("2f7c38b8-4a70-44fd-bf9c-ebf2b2aa78c0", "2023-06-01"),
            "TLH": ("db5bf699-31ea-44b6-91c5-1b41e4a2d130", "2023-06-01"),
            "MAS": ("101c443e-6500-4a4d-8cdc-6bd15f4388c8", "2023-12-01"),
            "SHG": ("bd7c477d-0742-4056-b75c-38b14ac7cf97", "2023-12-01"),
            "KET": ("a075cbc1-e920-455e-9d5a-8595061dfec0", "2024-06-01"),
            "LSB": ("ed0766c4-9270-4254-a107-eb4464a96ed9", "2025-06-01"),
            "SEH": ("0a4fdca5-2d78-4979-8051-10f21a216b16", "2025-06-01"),
            "RIB": ("10f0846e-d541-4340-81d1-e667cb5026ba", "2025-06-01"),
            "TOS": ("b564c8d6-a6c1-43d4-98d1-87ed8cd8ffd7", "2025-06-01"),
        },
    },
    "BN": {
        "org_id": "0123589c-7f1f-4eb4-8888-d8f8aa706ea4",
        "api_key": os.environ.get(
            "KOIOS_API_KEY_BN",
            os.environ.get("KOIOS_WRITE_API_KEY_BN", ""),
        ),
        "api_secret": os.environ.get(
            "KOIOS_API_SECRET_BN",
            os.environ.get("KOIOS_WRITE_API_SECRET_BN", ""),
        ),
        "sites": {
            "GBO": ("a23c334e-33f7-473d-9ae3-9e631d5336e4", "2025-06-01"),
            "SAM": ("8f80b0a8-0502-4e26-9043-7152979360aa", "2025-06-01"),
        },
    },
}

INITIAL_PER_PAGE = 50
MIN_PER_PAGE = 10
MAX_RETRIES = 5
BASE_TIMEOUT = 90


def fetch_day(session, org_cfg, site_id, date_str, per_page):
    """Fetch all readings for one site on one day. Returns (records, per_page_used).
    Adaptively reduces per_page on timeout/504."""
    url = f"{KOIOS_BASE}/api/v2/organizations/{org_cfg['org_id']}/data/historical"
    all_data = []
    cursor = None
    pp = per_page

    while True:
        body = {
            "filters": {
                "sites": [site_id],
                "date_range": {"from": date_str, "to": date_str},
            },
            "per_page": pp,
        }
        if cursor:
            body["cursor"] = cursor

        for attempt in range(MAX_RETRIES):
            wait = min(5 * (2 ** attempt), 60)
            try:
                r = session.post(url, json=body, timeout=BASE_TIMEOUT)
                if r.status_code in (500, 502, 503, 504):
                    if pp > MIN_PER_PAGE:
                        pp = max(pp // 2, MIN_PER_PAGE)
                        log.info("    HTTP %d, reducing per_page to %d (attempt %d)",
                                 r.status_code, pp, attempt + 1)
                        body["per_page"] = pp
                    else:
                        log.warning("    HTTP %d at per_page=%d, retry %d/%d",
                                    r.status_code, pp, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue
                if r.status_code == 400:
                    log.warning("    HTTP 400: %s", r.text[:200])
                    return all_data, pp
                r.raise_for_status()
                break
            except requests.exceptions.ReadTimeout:
                if pp > MIN_PER_PAGE:
                    pp = max(pp // 2, MIN_PER_PAGE)
                    log.info("    Timeout, reducing per_page to %d (attempt %d)",
                             pp, attempt + 1)
                    body["per_page"] = pp
                else:
                    log.warning("    Timeout at per_page=%d, retry %d/%d",
                                pp, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue
            except requests.exceptions.ConnectionError:
                log.warning("    Connection error, retry %d/%d", attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue
        else:
            log.warning("    Gave up on %s %s after %d attempts", site_id[:8], date_str, MAX_RETRIES)
            return all_data, pp

        resp = r.json()
        batch = resp.get("data", [])
        all_data.extend(batch)

        pag = resp.get("pagination", {})
        cursor = pag.get("cursor")
        if not pag.get("has_more") or not cursor or not batch:
            break
        time.sleep(0.5)

    return all_data, pp


def bin_to_hourly(records):
    """Aggregate raw Koios interval readings into hourly buckets per meter."""
    hourly = defaultdict(float)
    meter_acct = {}

    for rec in records:
        meter_obj = rec.get("meter", {})
        if not isinstance(meter_obj, dict):
            continue
        serial = meter_obj.get("serial_number", "")
        if not serial:
            continue

        cust = meter_obj.get("customer", {})
        if isinstance(cust, dict) and cust.get("code"):
            meter_acct[serial] = str(cust["code"]).strip()

        ts_str = rec.get("timestamp", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        hour_key = ts.strftime("%Y-%m-%d %H:00:00+00")

        kwh = 0.0
        for field in ("kilowatt_hours", "energy"):
            val = rec.get(field)
            if val is not None:
                try:
                    kwh = float(val)
                except (ValueError, TypeError):
                    pass
                break

        hourly[(serial, hour_key)] += kwh

    return [
        (serial, meter_acct.get(serial, ""), hour_str, kwh)
        for (serial, hour_str), kwh in hourly.items()
    ]


def get_staleness(conn, sites_to_run):
    """Return {site_code: latest_date_str} for koios-sourced data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT community, MAX(reading_hour)::date
        FROM hourly_consumption
        WHERE source = 'koios'
        GROUP BY community
    """)
    latest = {}
    for comm, dt in cur.fetchall():
        if comm in sites_to_run:
            latest[comm] = dt.strftime("%Y-%m-%d")
    cur.close()
    return latest


def check_freshness(org_cfg):
    """Query Koios v2 freshness endpoint. Returns {site_id: date_str} or empty on failure."""
    url = f"{KOIOS_BASE}/api/v2/organizations/{org_cfg['org_id']}/data/freshness"
    try:
        r = requests.post(
            url,
            json={},
            headers={
                "X-API-KEY": org_cfg["api_key"],
                "X-API-SECRET": org_cfg["api_secret"],
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning("Freshness check failed: HTTP %d", r.status_code)
            return {}
        fresh = r.json().get("freshness", {})
        result = {}
        for sid, val in fresh.items():
            if val and isinstance(val, dict) and val.get("reading"):
                result[sid] = val["reading"][:10]
        return result
    except Exception as e:
        log.warning("Freshness check error: %s", e)
        return {}


def import_site_day(session, org_cfg, site_code, site_id, date_str, meter_map, per_page):
    """Fetch and return processed batch for one site on one day."""
    raw, pp_used = fetch_day(session, org_cfg, site_id, date_str, per_page)
    if not raw:
        return [], pp_used

    hourly = bin_to_hourly(raw)
    if not hourly:
        return [], pp_used

    batch = []
    for serial, acct, hour_str, kwh in hourly:
        if not acct:
            info = meter_map.get(serial, {})
            acct = info.get("acct", serial)
        comm = meter_map.get(serial, {}).get("comm", site_code)
        batch.append((acct, serial, hour_str, round(kwh, 4), comm, "koios"))

    return batch, pp_used


def day_range(start_date, end_date):
    """Yield YYYY-MM-DD strings for each day in range."""
    d = start_date
    while d <= end_date:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def process_site(site_code, org_cfg, site_id, site_start_str,
                 start, end, meter_map, latest_dates, per_page):
    """Process all days for one site. Returns (site_code, total_inserted, batch_list)."""
    site_start = max(start, datetime.strptime(site_start_str, "%Y-%m-%d"))
    latest = latest_dates.get(site_code)

    session = requests.Session()
    session.headers.update({
        "X-API-KEY": org_cfg["api_key"],
        "X-API-SECRET": org_cfg["api_secret"],
    })

    all_batches = []
    pp = per_page

    for ds in day_range(site_start, end):
        if latest and ds < latest:
            continue
        log.info("  %s %s (per_page=%d)", site_code, ds, pp)
        try:
            batch, pp = import_site_day(session, org_cfg, site_code, site_id, ds, meter_map, pp)
            if batch:
                all_batches.append(batch)
                log.info("    +%d rows", len(batch))
            else:
                log.info("    (empty)")
        except Exception as e:
            log.error("  %s %s failed: %s", site_code, ds, e)

    session.close()
    return site_code, all_batches, pp


def main():
    parser = argparse.ArgumentParser(description="Import Koios hourly consumption")
    parser.add_argument("from_date", nargs="?", default=None)
    parser.add_argument("to_date", nargs="?",
                        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--site", help="Single site code (e.g. KET)")
    parser.add_argument("--country", default=None, help="Country code (LS, BN, or omit for all)")
    parser.add_argument("--reverse", action="store_true", help="Process newest-first")
    parser.add_argument("--no-aggregate", action="store_true",
                        help="Skip monthly aggregate rebuild")
    parser.add_argument("--workers", type=int, default=1,
                        help="Concurrent site workers (default 1)")
    parser.add_argument("--per-page", type=int, default=INITIAL_PER_PAGE,
                        help=f"Initial per_page (auto-reduces on failure, default {INITIAL_PER_PAGE})")
    args = parser.parse_args()

    if args.from_date is None:
        start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    else:
        start = datetime.strptime(args.from_date, "%Y-%m-%d")
    end = datetime.strptime(args.to_date, "%Y-%m-%d")

    countries = [args.country.upper()] if args.country else list(ORGS.keys())

    all_sites = {}
    for cc in countries:
        org_cfg = ORGS.get(cc)
        if not org_cfg:
            log.error("Unknown country: %s", cc)
            continue
        if not org_cfg["api_key"]:
            log.warning("No API key for %s — skipping", cc)
            continue
        for sc, (sid, ss) in org_cfg["sites"].items():
            if args.site and sc != args.site.upper():
                continue
            all_sites[sc] = (cc, org_cfg, sid, ss)

    if args.site and args.site.upper() not in all_sites:
        log.error("Unknown site: %s", args.site)
        sys.exit(1)

    log.info("=" * 60)
    log.info("HOURLY CONSUMPTION IMPORT FROM KOIOS")
    log.info("Range: %s to %s", start.strftime("%Y-%m-%d"), args.to_date)
    log.info("Sites: %s", ", ".join(sorted(all_sites)))
    log.info("Workers: %d, initial per_page: %d", args.workers, args.per_page)
    log.info("=" * 60)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT meter_id, account_number, community FROM meters")
    meter_map = {
        r[0]: {"acct": r[1] or "", "comm": r[2] or ""}
        for r in cur.fetchall()
    }

    latest_dates = get_staleness(conn, all_sites)
    for sc, dt in sorted(latest_dates.items()):
        log.info("  %s latest in DB: %s", sc, dt)

    freshness_by_org = {}
    for sc, (cc, org_cfg, sid, ss) in all_sites.items():
        oid = org_cfg["org_id"]
        if oid not in freshness_by_org:
            freshness_by_org[oid] = check_freshness(org_cfg)
            time.sleep(1)

    skipped = []
    for sc in list(all_sites.keys()):
        cc, org_cfg, sid, ss = all_sites[sc]
        oid = org_cfg["org_id"]
        api_fresh = freshness_by_org.get(oid, {}).get(sid)
        db_latest = latest_dates.get(sc)
        if api_fresh and db_latest and db_latest >= api_fresh:
            skipped.append(sc)
            log.info("  %s: DB (%s) >= API (%s) — skip", sc, db_latest, api_fresh)
    for sc in skipped:
        del all_sites[sc]

    if not all_sites:
        log.info("All sites up to date. Nothing to import.")
        conn.close()
        return

    grand_total = 0

    if args.workers <= 1:
        for sc in sorted(all_sites, key=lambda s: latest_dates.get(s, "0000")):
            cc, org_cfg, sid, ss = all_sites[sc]
            _, batches, _ = process_site(
                sc, org_cfg, sid, ss, start, end, meter_map, latest_dates, args.per_page
            )
            for batch in batches:
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO hourly_consumption
                        (account_number, meter_id, reading_hour, kwh, community, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meter_id, reading_hour) DO NOTHING
                """, batch, page_size=500)
                conn.commit()
                grand_total += len(batch)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            sorted_sites = sorted(all_sites, key=lambda s: latest_dates.get(s, "0000"))
            futures = {}
            for sc in sorted_sites:
                cc, org_cfg, sid, ss = all_sites[sc]
                f = pool.submit(
                    process_site,
                    sc, org_cfg, sid, ss, start, end, meter_map, latest_dates, args.per_page,
                )
                futures[f] = sc

            for f in as_completed(futures):
                sc = futures[f]
                try:
                    _, batches, _ = f.result()
                    for batch in batches:
                        psycopg2.extras.execute_batch(cur, """
                            INSERT INTO hourly_consumption
                                (account_number, meter_id, reading_hour, kwh, community, source)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (meter_id, reading_hour) DO NOTHING
                        """, batch, page_size=500)
                        conn.commit()
                        grand_total += len(batch)
                    log.info("--- %s done: %d rows ---", sc, sum(len(b) for b in batches))
                except Exception as e:
                    log.error("--- %s failed: %s ---", sc, e)

    log.info("=" * 60)
    log.info("GRAND TOTAL: %d hourly records", grand_total)
    log.info("=" * 60)

    if not args.no_aggregate and not args.site:
        log.info("Rebuilding monthly_consumption from hourly data...")
        cur.execute("TRUNCATE monthly_consumption;")
        cur.execute("""
            INSERT INTO monthly_consumption
                (account_number, meter_id, year_month, kwh, community, source)
            SELECT account_number, meter_id,
                   TO_CHAR(reading_hour, 'YYYY-MM'),
                   SUM(kwh), community, 'import'::transaction_source
            FROM hourly_consumption
            GROUP BY account_number, meter_id,
                     TO_CHAR(reading_hour, 'YYYY-MM'), community;
        """)
        conn.commit()
        cur.execute("SELECT count(*) FROM monthly_consumption;")
        log.info("  monthly_consumption: %d rows", cur.fetchone()[0])

        log.info("Rebuilding monthly_transactions from transaction data...")
        cur.execute("TRUNCATE monthly_transactions;")
        cur.execute("""
            INSERT INTO monthly_transactions
                (account_number, meter_id, year_month, kwh_vended,
                 amount_lsl, txn_count, community, source)
            SELECT t.account_number, t.meter_id,
                   TO_CHAR(t.transaction_date, 'YYYY-MM'),
                   SUM(COALESCE(t.kwh_value, 0)),
                   SUM(COALESCE(t.transaction_amount, 0)),
                   COUNT(*),
                   COALESCE(m.community, ''),
                   'import'::transaction_source
            FROM transactions t
            LEFT JOIN meters m ON t.meter_id = m.meter_id
            GROUP BY t.account_number, t.meter_id,
                     TO_CHAR(t.transaction_date, 'YYYY-MM'),
                     m.community;
        """)
        conn.commit()
        cur.execute("SELECT count(*) FROM monthly_transactions;")
        log.info("  monthly_transactions: %d rows", cur.fetchone()[0])
    else:
        log.info("Skipping aggregate rebuild (--no-aggregate or --site mode)")

    log.info("DONE.")
    conn.close()


if __name__ == "__main__":
    main()
