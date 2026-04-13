#!/usr/bin/env python3
"""
Fix MAK customer-account mapping drift between 1PDB and ThunderCloud.

ThunderCloud (SparkMeter) is the metering authority for MAK.
This script:
1. Identifies name mismatches (different person on same account code)
2. Generates SQL to update 1PDB customer names to match TC
3. Optionally applies the fixes

Usage:
  python3 fix_mak_drift.py          # Report only
  python3 fix_mak_drift.py --apply  # Apply fixes
"""
import os, sys, re, psycopg2, requests

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

APPLY = "--apply" in sys.argv

# ---- Fetch ThunderCloud ----
TC_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
TC_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
r = requests.get(
    TC_BASE + "/api/v0/customers",
    params={"customers_only": "false", "reading_details": "false"},
    headers={"Authentication-Token": TC_TOKEN},
    timeout=60,
)
tc_customers = r.json().get("customers", [])

tc_by_code = {}
for c in tc_customers:
    code = c.get("code", "").strip()
    if code and "MAK" in code:
        name = c.get("name", "").strip()
        name = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
        meters = [m.get("serial", "") for m in c.get("meters", [])]
        tc_by_code[code] = {"name": name, "meters": meters}

# ---- Fetch 1PDB ----
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute(
    "SELECT a.account_number, c.first_name, c.last_name, c.id "
    "FROM accounts a "
    "JOIN customers c ON c.id = a.customer_id "
    "WHERE a.community = 'MAK' "
    "ORDER BY a.account_number"
)
pdb_by_code = {}
for acct, fn, ln, cid in cur.fetchall():
    name = ((fn or "") + " " + (ln or "")).strip()
    pdb_by_code[acct] = {"name": name, "cust_id": cid, "fn": fn, "ln": ln}

# ---- Find real mismatches ----
def name_tokens(n):
    return set(n.lower().split())

mismatches = []
for code in sorted(set(tc_by_code) & set(pdb_by_code)):
    pn = pdb_by_code[code]["name"]
    tn = tc_by_code[code]["name"]
    pt = name_tokens(pn)
    tt = name_tokens(tn)
    overlap = len(pt & tt)
    if overlap < max(1, min(len(pt), len(tt)) * 0.5):
        mismatches.append(code)

print("=" * 70)
print("MAK ACCOUNT-CUSTOMER DRIFT REPORT")
print("=" * 70)
print("\nReal mismatches (different person on same account): %d" % len(mismatches))
print()

for code in mismatches:
    pdb = pdb_by_code[code]
    tc = tc_by_code[code]

    # Parse TC name into first/last
    tc_parts = tc["name"].split()
    if len(tc_parts) >= 2:
        tc_fn = tc_parts[0]
        tc_ln = " ".join(tc_parts[1:])
    else:
        tc_fn = tc["name"]
        tc_ln = ""

    print("  %s:" % code)
    print("    1PDB: %s (cust_id=%d)" % (pdb["name"], pdb["cust_id"]))
    print("    TC:   %s" % tc["name"])
    print("    Fix:  UPDATE customers SET first_name='%s', last_name='%s' WHERE id=%d"
          % (tc_fn, tc_ln, pdb["cust_id"]))
    print()

    if APPLY:
        cur.execute(
            "UPDATE customers SET first_name = %s, last_name = %s WHERE id = %s",
            (tc_fn, tc_ln, pdb["cust_id"]),
        )
        print("    -> APPLIED (%d row)" % cur.rowcount)

# ---- Accounts in TC but not 1PDB ----
tc_only = sorted(set(tc_by_code) - set(pdb_by_code))
if tc_only:
    print("\nAccounts in TC but NOT in 1PDB: %d" % len(tc_only))
    for code in tc_only:
        print("  %s: %s" % (code, tc_by_code[code]["name"]))

# ---- Accounts in 1PDB but not TC ----
pdb_only = sorted(set(pdb_by_code) - set(tc_by_code))
if pdb_only:
    print("\nAccounts in 1PDB but NOT in TC: %d (likely 1Meter/pending)" % len(pdb_only))
    for code in pdb_only:
        print("  %s: %s" % (code, pdb_by_code[code]["name"]))

if APPLY:
    conn.commit()
    print("\n*** Changes committed ***")
else:
    print("\n*** DRY RUN - use --apply to commit changes ***")

cur.close()
conn.close()
