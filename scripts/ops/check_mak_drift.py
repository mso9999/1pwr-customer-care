#!/usr/bin/env python3
"""Check MAK account discrepancies between 1PDB and ThunderCloud."""
import os, sys, psycopg2

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute(
    "SELECT a.account_number, c.first_name, c.last_name, m.meter_id, m.role "
    "FROM accounts a "
    "JOIN customers c ON c.id = a.customer_id "
    "LEFT JOIN meters m ON m.account_number = a.account_number "
    "WHERE a.community = 'MAK' "
    "AND CAST(SUBSTRING(a.account_number FROM 1 FOR 4) AS INTEGER) >= 218 "
    "ORDER BY a.account_number"
)
print("MAK accounts from 0218 onward:")
for acct, fn, ln, mid, role in cur.fetchall():
    name = ((fn or "") + " " + (ln or "")).strip()
    meter = mid if mid else "(none)"
    r = role if role else ""
    print("  %-10s  %-28s  %-25s %s" % (acct, name, meter, r))

cur.execute(
    "SELECT COUNT(*) FROM meters "
    "WHERE community = 'MAK' AND meter_id LIKE 'SMRS%%'"
)
sm = cur.fetchone()[0]
cur.execute(
    "SELECT COUNT(*) FROM meters "
    "WHERE community = 'MAK' AND meter_id ~ '^[0-9]+$'"
)
num = cur.fetchone()[0]
cur.execute(
    "SELECT COUNT(*) FROM meters "
    "WHERE community = 'MAK' AND meter_id LIKE 'ACCT-%%'"
)
acct_type = cur.fetchone()[0]
print("\nMeter types: %d SparkMeter(SMRS), %d numeric(old), %d 1Meter(ACCT-)" % (sm, num, acct_type))

cur.close()
conn.close()
