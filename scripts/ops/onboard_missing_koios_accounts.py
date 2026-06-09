#!/usr/bin/env python3
"""Back-sync (Koios -> CC) customers that exist on Koios but have no CC account.

For BN (onepower_bj) we found genuine GBO/SAM customers present in Koios (metered,
consuming) but with no CC customer/account/meter record. This creates the CC records to
match Koios (the codes + meter serials already exist on Koios, so this does NOT push back
to SparkMeter). Idempotent: skips any code that already has an accounts row. Dry-run by
default; pass --apply to write.

Conventions matched to existing BN rows: customer_type='SME', customer_commissioned=False,
date_service_connected=NULL (the BN team commissions formally via the portal afterwards).
Creating the account also "adopts" any orphan transactions already keyed to the code.

Run on the CC host with DATABASE_URL pointed at the country DB (e.g. DATABASE_URL_BN):
    DATABASE_URL=$DATABASE_URL_BN venv/bin/python3 onboard_missing_koios_accounts.py [--apply]
"""
from __future__ import annotations

import os
import re
import sys

import psycopg2

CREATED_BY = "koios_backsync_20260609"

# (koios_code, koios_name, meter_serial) — pulled from Koios v1 /customers?code 2026-06-09.
ACCOUNTS = [
    ("0024GBO", "BOCO Guy",          "SMRSD-04-0008FBF3"),
    ("0041GBO", "ASSOGBA Alexis",    "SMRSD-04-0008FC45"),
    ("0046GBO", "ADAMOU Mohamed",    "SMRSD-04-0008FC40"),
    ("0047GBO", "ADAM Aboudoullah",  "SMRSD-04-0008FBE8"),
    ("0062GBO", "AZALOU Robert",     "SMRSD-04-0008F184"),
    ("0063GBO", "ATCHASSOU Andre",   "SMRSD-04-0008FBD0"),
    ("0119GBO", "AGBETOU RACHELLE",  "SMRSD-04-0008FBDC"),
    ("0138GBO", "IBRAHIM YOUSSOUF",  "SMRSD-04-0008FC35"),
    ("0055SAM", "FORAGE",            "SMRSD-04-0008FBFB"),
]

APPLY = "--apply" in sys.argv


def split_name(name: str) -> tuple[str, str]:
    toks = (name or "").split()
    if len(toks) >= 2:
        return " ".join(toks[1:]), toks[0]  # (first_name, last_name) — "SURNAME Given"
    return "", (toks[0] if toks else "UNKNOWN")


def site_of(code: str) -> str:
    m = re.search(r"([A-Z]{2,4})$", code.upper())
    return m.group(1) if m else ""


def main() -> int:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = False
    cur = conn.cursor()
    created = skipped = 0
    for code, name, serial in ACCOUNTS:
        code = code.upper()
        community = site_of(code)
        cur.execute("SELECT 1 FROM accounts WHERE upper(account_number)=%s", (code,))
        if cur.fetchone():
            print(f"SKIP {code}: account already exists")
            skipped += 1
            continue
        first, last = split_name(name)
        seq = int(code[:4])
        print(f"{'CREATE' if APPLY else 'PLAN'} {code}: {last} {first} | meter={serial} | site={community} seq={seq}")
        if not APPLY:
            continue
        cur.execute(
            """
            INSERT INTO customers (first_name, last_name, community, country, customer_type,
                                   is_active, created_by, updated_by)
            VALUES (%s, %s, %s, 'Benin', 'SME', true, %s, %s)
            RETURNING id
            """,
            (first, last, community, CREATED_BY, CREATED_BY),
        )
        cust_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO accounts (account_number, customer_id, meter_id, community,
                                  account_sequence, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (code, cust_id, serial, community, seq, CREATED_BY),
        )
        cur.execute("SELECT 1 FROM meters WHERE meter_id=%s", (serial,))
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO meters (meter_id, account_number, community, role, status)
                VALUES (%s, %s, %s, 'primary', 'active'::meter_status)
                """,
                (serial, code, community),
            )
        created += 1

    if APPLY:
        conn.commit()
        print(f"APPLIED: created={created} skipped={skipped}")
    else:
        conn.rollback()
        print(f"DRY RUN: would_create={len(ACCOUNTS)-skipped} skipped={skipped} (use --apply)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
