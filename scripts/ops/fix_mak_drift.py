#!/usr/bin/env python3
"""
Fix MAK customer-account mapping drift between 1PDB and ThunderCloud.

CC/1PDB is the source of truth for ongoing edits; when TC was verified correct
and CC was wrong (e.g. after migration), sync 1PDB from TC here.

Default mode uses a **strict token-overlap** heuristic (only flags “different
person” pairs). That misses many rows where CC and TC disagree but still share
a surname token — use **--sync-all-from-tc** for a full string alignment.

Usage:
  python3 fix_mak_drift.py                    # Report only (token heuristic)
  python3 fix_mak_drift.py --apply            # Apply token-heuristic fixes only
  python3 fix_mak_drift.py --sync-all-from-tc # Report: every code where full name != TC
  python3 fix_mak_drift.py --sync-all-from-tc --apply   # Apply all TC -> 1PDB name updates
"""
import os, sys, re, psycopg2, requests

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

APPLY = "--apply" in sys.argv
SYNC_ALL = "--sync-all-from-tc" in sys.argv

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
    code = c.get("code", "").strip().upper()
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


def norm_full(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def tc_name_to_first_last(tc_name: str):
    tc_name = (tc_name or "").strip()
    parts = tc_name.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return tc_name, ""


mismatches = []
for code in sorted(set(tc_by_code) & set(pdb_by_code)):
    pn = pdb_by_code[code]["name"]
    tn = tc_by_code[code]["name"]
    if SYNC_ALL:
        if norm_full(pn) != norm_full(tn):
            mismatches.append(code)
    else:
        pt = name_tokens(pn)
        tt = name_tokens(tn)
        overlap = len(pt & tt)
        if overlap < max(1, min(len(pt), len(tt)) * 0.5):
            mismatches.append(code)

print("=" * 70)
print("MAK ACCOUNT-CUSTOMER DRIFT REPORT")
if SYNC_ALL:
    print("(mode: --sync-all-from-tc: full name string vs TC)")
else:
    print("(mode: token-overlap heuristic; use --sync-all-from-tc for full alignment)")
print("=" * 70)
print("\nMismatches to fix: %d" % len(mismatches))
print()

for code in mismatches:
    pdb = pdb_by_code[code]
    tc = tc_by_code[code]

    tc_fn, tc_ln = tc_name_to_first_last(tc["name"])

    print("  %s:" % code)
    print("    1PDB: %s (cust_id=%d)" % (pdb["name"], pdb["cust_id"]))
    print("    TC:   %s" % tc["name"])
    print("    Fix:  UPDATE customers SET first_name=..., last_name=... WHERE id=%d"
          % (pdb["cust_id"]))
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
    if SYNC_ALL:
        print("\n*** DRY RUN - use --sync-all-from-tc --apply to commit ***")
    else:
        print("\n*** DRY RUN - use --apply to commit (or --sync-all-from-tc for full list) ***")

cur.close()
conn.close()
