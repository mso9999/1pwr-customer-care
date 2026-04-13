#!/usr/bin/env python3
"""
Fix ALL customer-account name mismatches between 1PDB and SparkMeter.
SparkMeter (ThunderCloud + Koios) is the metering authority.

Usage:
  python3 fix_all_sites_drift.py          # Dry run
  python3 fix_all_sites_drift.py --apply  # Apply fixes
"""
import os, sys, re, psycopg2, requests

APPLY = "--apply" in sys.argv

with open("/opt/1pdb/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

sm_by_code = {}

# ---- ThunderCloud (MAK) ----
TC_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
TC_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
r = requests.get(
    TC_BASE + "/api/v0/customers",
    params={"customers_only": "false", "reading_details": "false"},
    headers={"Authentication-Token": TC_TOKEN},
    timeout=60,
)
for c in r.json().get("customers", []):
    code = (c.get("code") or "").strip()
    name = (c.get("name") or "").strip()
    if code and name:
        sm_by_code[code] = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()

# ---- Koios web session ----
KOIOS_BASE = "https://www.sparkmeter.cloud"
KOIOS_ORG = os.environ.get("KOIOS_ORG_ID", "1cddcb07-6647-40aa-aaaa-70d762922029")
KOIOS_EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "")
KOIOS_PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "")

session = requests.Session()
r = session.get(KOIOS_BASE + "/login", timeout=30)
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
session.post(
    KOIOS_BASE + "/login",
    data={"csrf_token": csrf.group(1), "email": KOIOS_EMAIL, "password": KOIOS_PASSWORD},
    timeout=30,
)

page = 1
while True:
    r = session.get(
        "%s/sm/organizations/%s/customers" % (KOIOS_BASE, KOIOS_ORG),
        headers={"Accept": "application/json"},
        params={"page_size": 500, "page": page},
        timeout=120,
    )
    if r.status_code != 200:
        break
    customers = r.json().get("customers", [])
    if not customers:
        break
    for c in customers:
        code = (c.get("code") or "").strip()
        name = (c.get("name") or "").strip()
        if code and name:
            sm_by_code[code] = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
    if len(customers) < 500:
        break
    page += 1

print("SparkMeter: %d customers loaded" % len(sm_by_code))

# ---- 1PDB ----
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT a.account_number, c.first_name, c.last_name, c.id, a.community
    FROM accounts a
    JOIN customers c ON c.id = a.customer_id
    ORDER BY a.community, a.account_number
""")

def name_tokens(n):
    return set(n.lower().split())

fixes = []
for acct, fn, ln, cid, comm in cur.fetchall():
    pdb_name = ((fn or "") + " " + (ln or "")).strip()
    sm_name = sm_by_code.get(acct)
    if not sm_name:
        continue
    pt = name_tokens(pdb_name)
    tt = name_tokens(sm_name)
    overlap = len(pt & tt)
    threshold = max(1, min(len(pt), len(tt)) * 0.5)
    if overlap < threshold:
        # Parse SM name into first/last
        parts = sm_name.split()
        if len(parts) >= 2:
            new_fn = parts[0]
            new_ln = " ".join(parts[1:])
        else:
            new_fn = sm_name
            new_ln = ""
        fixes.append((acct, comm, cid, fn, ln, new_fn, new_ln, pdb_name, sm_name))

print("Mismatches to fix: %d\n" % len(fixes))

# Group by site
from collections import defaultdict
by_site = defaultdict(list)
for f in fixes:
    by_site[f[1]].append(f)

applied = 0
for site in sorted(by_site):
    site_fixes = by_site[site]
    print("--- %s (%d) ---" % (site, len(site_fixes)))
    for acct, comm, cid, old_fn, old_ln, new_fn, new_ln, pdb_name, sm_name in site_fixes:
        print("  %s: '%s' -> '%s %s'" % (acct, pdb_name, new_fn, new_ln))
        if APPLY:
            cur.execute(
                "UPDATE customers SET first_name = %s, last_name = %s WHERE id = %s",
                (new_fn, new_ln, cid),
            )
            applied += cur.rowcount

if APPLY:
    conn.commit()
    print("\n*** Applied %d fixes ***" % applied)
else:
    print("\n*** DRY RUN — use --apply to commit ***")

cur.close()
conn.close()
