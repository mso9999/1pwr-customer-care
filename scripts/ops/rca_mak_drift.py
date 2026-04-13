#!/usr/bin/env python3
"""RCA: How did 1PDB get out of sync with ThunderCloud for MAK?"""
import os, sys, psycopg2, requests
from datetime import datetime

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# 1. When were mismatched customers created in 1PDB?
print("=" * 70)
print("1. CUSTOMER CREATION TIMELINE IN 1PDB")
print("=" * 70)
cur.execute("""
    SELECT a.account_number, c.first_name, c.last_name, c.id,
           c.created_at
    FROM accounts a
    JOIN customers c ON c.id = a.customer_id
    WHERE a.community = 'MAK'
    AND CAST(SUBSTRING(a.account_number FROM 1 FOR 4) AS INTEGER) >= 200
    ORDER BY a.account_number
""")
rows = cur.fetchall()
print("\nAccounts 0200+ creation dates:")
for acct, fn, ln, cid, c_created in rows:
    name = ((fn or "") + " " + (ln or "")).strip()
    c_dt = str(c_created)[:19] if c_created else "NULL"
    print("  %s  %-28s  id=%d  created=%s" % (acct, name, cid, c_dt))

# 2. How does customer data get into 1PDB for MAK?
print("\n" + "=" * 70)
print("2. DATA SOURCES - How did MAK customers get into 1PDB?")
print("=" * 70)

# Check if there's a source column or import log
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'customers' AND column_name IN ('source', 'import_source', 'created_by', 'migrated_from')
""")
source_cols = [r[0] for r in cur.fetchall()]
print("\nCustomer table source columns: %s" % (source_cols or "(none)"))

# Check for any migration/import log tables
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public'
    AND (table_name LIKE '%%migration%%' OR table_name LIKE '%%import%%' OR table_name LIKE '%%sync%%')
""")
log_tables = [r[0] for r in cur.fetchall()]
print("Migration/import/sync tables: %s" % (log_tables or "(none)"))

# 3. Check if there's a TC customer sync process
print("\n" + "=" * 70)
print("3. THUNDERCLOUD SYNC MECHANISM")
print("=" * 70)

# Check if TC customer data is ever imported
cur.execute("""
    SELECT DISTINCT source FROM transactions
    WHERE account_number LIKE '%%MAK'
    ORDER BY source
""")
tx_sources = [r[0] for r in cur.fetchall()]
print("\nTransaction sources for MAK: %s" % tx_sources)

# 4. Compare the boundary - where does the drift start?
print("\n" + "=" * 70)
print("4. DRIFT BOUNDARY ANALYSIS")
print("=" * 70)

TC_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
TC_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
r = requests.get(
    TC_BASE + "/api/v0/customers",
    params={"customers_only": "false", "reading_details": "false"},
    headers={"Authentication-Token": TC_TOKEN},
    timeout=60,
)
tc_by_code = {}
for c in r.json().get("customers", []):
    code = c.get("code", "").strip()
    if code and "MAK" in code:
        tc_by_code[code] = c.get("name", "").strip()

cur.execute("""
    SELECT a.account_number, c.first_name, c.last_name
    FROM accounts a JOIN customers c ON c.id = a.customer_id
    WHERE a.community = 'MAK' ORDER BY a.account_number
""")

def norm(n):
    return set(n.lower().split())

last_match = None
first_mismatch = None
for acct, fn, ln in cur.fetchall():
    pn = ((fn or "") + " " + (ln or "")).strip()
    tn = tc_by_code.get(acct, "")
    if tn:
        if len(norm(pn) & norm(tn)) >= max(1, min(len(norm(pn)), len(norm(tn))) * 0.5):
            last_match = acct
        else:
            if not first_mismatch:
                first_mismatch = (acct, pn, tn)

print("\nLast matching account: %s" % last_match)
print("First mismatched account: %s -> 1PDB='%s' TC='%s'" % first_mismatch if first_mismatch else "None")

# 5. Check if the old customers (displaced by drift) exist elsewhere
print("\n" + "=" * 70)
print("5. WHERE DID THE DISPLACED 1PDB CUSTOMERS GO?")
print("=" * 70)

displaced = [
    (1339, "Malitaba", "Mara", "0218MAK"),
    (1400, "Macobone", "Mocase", "0219MAK"),
    (1401, "Mafumane", "Liphoto", "0220MAK"),
    (1402, "Kenalemang", "Mokhothu", "0221MAK"),
    (3010, "Teboho", "Lehlokoanyane", "0297MAK"),
]
for cid, fn, ln, was_on in displaced:
    # Find all accounts for this customer
    cur.execute("SELECT account_number FROM accounts WHERE customer_id = %s", (cid,))
    all_accts = [r[0] for r in cur.fetchall()]
    # Find if this name exists in TC
    tc_match = [(code, name) for code, name in tc_by_code.items()
                if ln.lower() in name.lower() or fn.lower() in name.lower()]
    print("  %s %s (id=%d, was on %s):" % (fn, ln, cid, was_on))
    print("    1PDB accounts: %s" % all_accts)
    print("    TC matches: %s" % (tc_match or "NONE"))

# 6. Check customer_id_legacy for clues
print("\n" + "=" * 70)
print("6. LEGACY ID ANALYSIS")
print("=" * 70)
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'customers' AND column_name LIKE '%%legacy%%'
""")
legacy_cols = [r[0] for r in cur.fetchall()]
if legacy_cols:
    cur.execute("""
        SELECT a.account_number, c.first_name, c.last_name, c.customer_id_legacy
        FROM accounts a JOIN customers c ON c.id = a.customer_id
        WHERE a.community = 'MAK'
        AND CAST(SUBSTRING(a.account_number FROM 1 FOR 4) AS INTEGER) BETWEEN 215 AND 230
        ORDER BY a.account_number
    """)
    for acct, fn, ln, legacy in cur.fetchall():
        name = ((fn or "") + " " + (ln or "")).strip()
        print("  %s  %-28s  legacy_id=%s" % (acct, name, legacy))

cur.close()
conn.close()
