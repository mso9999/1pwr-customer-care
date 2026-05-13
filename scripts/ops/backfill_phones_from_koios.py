"""Backfill customer phone numbers from Koios — query per-account with parallelism."""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import requests

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
KOIOS_KEY = os.environ.get("KOIOS_API_KEY", "")
KOIOS_SECRET = os.environ.get("KOIOS_API_SECRET", "")
HEADERS = {"X-API-KEY": KOIOS_KEY, "X-API-SECRET": KOIOS_SECRET}
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)

COALESCE_PHONE_SQL = """
COALESCE(
    NULLIF(TRIM(c.cell_phone_1), ''),
    NULLIF(TRIM(c.phone), ''),
    NULLIF(TRIM(c.cell_phone_2), '')
)
"""


def lookup_koios_phone(acct):
    """Return (account_number, phone_digits) or (account_number, None)."""
    try:
        r = requests.get(
            f"{KOIOS_BASE}/api/v1/customers",
            headers=HEADERS,
            params={"code": acct},
            timeout=30,
        )
        data = r.json().get("data", [])
        if data:
            phone = (data[0].get("phone_number") or "").strip()
            digits = "".join(ch for ch in phone if ch.isdigit())
            if len(digits) >= 8:
                return (acct, digits)
        return (acct, None)
    except Exception as e:
        return (acct, f"ERROR:{e}")


def main():
    do_apply = "--apply" in sys.argv

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Step 1: Get accounts needing backfill
    print("=== Fetching accounts needing backfill ===")
    cur.execute(
        f"""
        SELECT a.account_number, c.id AS customer_id
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE EXISTS (
            SELECT 1 FROM meters m
            WHERE m.account_number = a.account_number AND m.status = 'active'
        )
        AND LENGTH(regexp_replace({COALESCE_PHONE_SQL}, '[^0-9]', '', 'g')) < 8
        """
    )
    need_backfill = [(row[0], row[1]) for row in cur.fetchall()]
    print(f"Accounts needing backfill: {len(need_backfill)}")

    if not need_backfill:
        print("Nothing to do.")
        cur.close()
        conn.close()
        return

    # Build a lookup dict: account_number -> customer_id
    acct_to_cust = {acct: cust_id for acct, cust_id in need_backfill}

    # Step 2: Query Koios in parallel
    print(f"\n=== Querying Koios (parallel, 10 workers) ===")
    found = 0
    errors = 0
    missing = 0
    updates = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(lookup_koios_phone, acct): acct for acct in acct_to_cust
        }
        done = 0
        for future in as_completed(futures):
            acct, result = future.result()
            done += 1
            if done % 100 == 0:
                elapsed = time.time() - start
                print(
                    f"  {done}/{len(need_backfill)} ({elapsed:.0f}s) — "
                    f"found: {found}, missing: {missing}, errors: {errors}"
                )
            if result is None:
                missing += 1
            elif result.startswith("ERROR:"):
                errors += 1
            else:
                found += 1
                updates.append((result, acct_to_cust[acct]))

    elapsed = time.time() - start
    print(f"\nDone querying in {elapsed:.0f}s")
    print(f"  Found in Koios: {found}")
    print(f"  Not in Koios:  {missing}")
    print(f"  Errors:         {errors}")

    # Step 3: Apply updates
    if do_apply:
        if not updates:
            print("No updates to apply.")
        else:
            print(f"\n=== Applying {len(updates)} updates ===")
            cur2 = conn.cursor()
            for phone, cust_id in updates:
                cur2.execute(
                    "UPDATE customers SET cell_phone_1 = %s WHERE id = %s",
                    (phone, cust_id),
                )
            conn.commit()
            cur2.close()
            print(f"Updated {len(updates)} customer records.")
    else:
        print(f"\n=== DRY RUN — {len(updates)} would be updated ===")
        for phone, cust_id in updates[:5]:
            print(f"  customer_id={cust_id} -> cell_phone_1='{phone}'")
        if len(updates) > 5:
            print(f"  ... and {len(updates) - 5} more")
        print("\nRun with --apply to execute the updates.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
