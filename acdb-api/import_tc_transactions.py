"""
Import MAK transactions from ThunderCloud's internal web API in real-time.

Unlike backfill_mak_transactions.py (which derives payments from parquet balance
jumps with ~1 day lag), this pulls actual transaction records via the session-
authenticated /transaction/transactions.json endpoint — same data the dashboard shows.

Usage:
    python3 import_tc_transactions.py                # last 7 days
    python3 import_tc_transactions.py --days 30      # last 30 days
    python3 import_tc_transactions.py --dry-run      # preview only
"""
import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

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
log = logging.getLogger("tc_txn")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api:gKkYLkzYwSRPNoSwuC87YVqbzCmnhI4e@localhost:5432/onepower_cc",
)
SC_BASE = os.environ.get(
    "TC_API_BASE",
    "https://sparkcloud-u740425.sparkmeter.cloud",
)
TC_EMAIL = os.environ.get("THUNDERCLOUD_USERNAME", "makhoalinyane@1pwrafrica.com")
TC_PASS = os.environ.get("THUNDERCLOUD_PASSWORD", "00001111")
COMMUNITY = "MAK"


def sc_login(session):
    """Login to SparkCloud dashboard via CSRF form auth."""
    r1 = session.get(f"{SC_BASE}/login", timeout=30, verify=False)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r1.text)
    if not m:
        log.error("Could not find CSRF token on login page")
        return False
    r2 = session.post(f"{SC_BASE}/login", data={
        "csrf_token": m.group(1),
        "email": TC_EMAIL,
        "password": TC_PASS,
    }, timeout=30, verify=False, allow_redirects=True)
    if "/login" in r2.url:
        log.error("Login failed — check credentials")
        return False
    log.info("SparkCloud login OK")
    return True


def fetch_transactions(session, cutoff_dt, page_size=100, max_pages=200):
    """Fetch recent transactions from the internal API until we pass the cutoff date.
    Transactions are returned newest-first, so we stop early."""
    all_txns = []
    start = 0
    while start < page_size * max_pages:
        url = (f"{SC_BASE}/transaction/transactions.json"
               f"?start={start}&length={page_size}")
        r = session.get(url, timeout=30, verify=False)
        if r.status_code != 200:
            log.warning("HTTP %d fetching transactions at offset %d", r.status_code, start)
            break
        data = r.json()
        batch = data.get("transactions", [])
        total = data.get("total", 0)
        all_txns.extend(batch)
        log.info("  Fetched %d/%d transactions (offset %d)", len(all_txns), total, start)
        if len(all_txns) >= total or not batch:
            break
        oldest_in_batch = batch[-1].get("created", "")
        if oldest_in_batch:
            try:
                oldest_dt = datetime.fromisoformat(oldest_in_batch.replace("Z", "+00:00"))
                if oldest_dt.tzinfo is None:
                    oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
                if oldest_dt < cutoff_dt:
                    log.info("  Reached cutoff date, stopping pagination")
                    break
            except (ValueError, TypeError):
                pass
        start += page_size
        time.sleep(0.3)
    return all_txns


def main():
    parser = argparse.ArgumentParser(description="Import MAK transactions from ThunderCloud API")
    parser.add_argument("--days", type=int, default=7, help="Import transactions from last N days")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    log.info("=" * 60)
    log.info("THUNDERCLOUD LIVE TRANSACTION IMPORT")
    log.info("Cutoff: %s (%d days)", cutoff.strftime("%Y-%m-%d %H:%M"), args.days)
    log.info("=" * 60)

    session = requests.Session()
    if not sc_login(session):
        sys.exit(1)

    # Also fetch meter map for serial → account_number
    r_meters = session.get(f"{SC_BASE}/meter/meters.json?meter_type=customer",
                           timeout=30, verify=False)
    meter_map = {}
    if r_meters.status_code == 200:
        for m in r_meters.json().get("meters", []):
            serial = m.get("meter_serial", "")
            code = m.get("customer_code", "")
            if serial and code:
                meter_map[serial] = code
        log.info("Meter map: %d meters", len(meter_map))

    raw_txns = fetch_transactions(session, cutoff)
    log.info("Total raw transactions: %d", len(raw_txns))

    # Filter to credit transactions within our date range
    records = []
    for t in raw_txns:
        if t.get("acct_type") != "credit":
            continue
        amount = t.get("amount", 0)
        if not amount or amount <= 0:
            continue
        created = t.get("created", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            continue

        to_data = t.get("to_data", {}) or {}
        meter_serial = to_data.get("meter_serial", "")
        customer_code = to_data.get("customer_code", "")
        if not customer_code and meter_serial:
            customer_code = meter_map.get(meter_serial, "")
        if not customer_code:
            continue

        records.append({
            "account_number": customer_code,
            "meter_id": meter_serial,
            "transaction_date": dt,
            "transaction_amount": round(amount, 4),
            "external_id": t.get("external_id", ""),
            "source": "thundercloud",
            "community": COMMUNITY,
        })

    log.info("Credit transactions in range: %d", len(records))
    if not records:
        log.info("Nothing to import.")
        return

    if args.dry_run:
        log.info("DRY RUN — would insert %d records:", len(records))
        for r in records[:10]:
            log.info("  %s %s M%.2f %s",
                     r["account_number"], r["transaction_date"], r["transaction_amount"],
                     r["external_id"][:20])
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    import hashlib
    batch = []
    for r in records:
        ext = r["external_id"] or ""
        dedup = ext[:50] if ext else hashlib.md5(
            f"{r['account_number']}|{r['transaction_date'].isoformat()}|{r['transaction_amount']}"
            .encode()
        ).hexdigest()[:16]
        batch.append((
            r["account_number"], r["meter_id"], r["transaction_date"],
            r["transaction_amount"], True, r["source"], dedup,
        ))

    psycopg2.extras.execute_batch(cur, """
        INSERT INTO transactions
            (account_number, meter_id, transaction_date, transaction_amount,
             is_payment, source, source_table)
        VALUES (%s, %s, %s, %s, %s, %s::transaction_source, %s)
        ON CONFLICT DO NOTHING
    """, batch, page_size=200)

    inserted = cur.rowcount
    conn.commit()
    log.info("Inserted %d / %d transactions (rest were duplicates)", inserted, len(batch))

    conn.close()
    log.info("DONE.")


if __name__ == "__main__":
    main()
