#!/usr/bin/env python3
"""
Audit ALL sites for customer-account drift between 1PDB and SparkMeter.
Checks ThunderCloud (MAK) and Koios (all other LS sites).
"""
import os, sys, re, psycopg2, requests

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

print("ThunderCloud: %d customers" % len(sm_by_code))

# ---- Koios web session (all LS sites except MAK) ----
KOIOS_BASE = "https://www.sparkmeter.cloud"
KOIOS_ORG = os.environ.get("KOIOS_ORG_ID", "1cddcb07-6647-40aa-aaaa-70d762922029")
KOIOS_EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "")
KOIOS_PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "")

session = requests.Session()
r = session.get(KOIOS_BASE + "/login", timeout=30)
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
if not csrf:
    print("ERROR: CSRF token not found")
    sys.exit(1)
r = session.post(
    KOIOS_BASE + "/login",
    data={"csrf_token": csrf.group(1), "email": KOIOS_EMAIL, "password": KOIOS_PASSWORD},
    timeout=30,
)
print("Koios login: %s" % ("OK" if "/login" not in r.url else "FAILED"))

# Paginate through ALL Koios customers
page = 1
koios_total = 0
while True:
    r = session.get(
        "%s/sm/organizations/%s/customers" % (KOIOS_BASE, KOIOS_ORG),
        headers={"Accept": "application/json"},
        params={"page_size": 500, "page": page},
        timeout=120,
    )
    if r.status_code != 200:
        print("  Page %d: HTTP %d" % (page, r.status_code))
        break
    data = r.json()
    customers = data.get("customers", [])
    if not customers:
        break
    for c in customers:
        code = (c.get("code") or "").strip()
        name = (c.get("name") or "").strip()
        if code and name:
            sm_by_code[code] = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
            koios_total += 1
    print("  Page %d: %d customers (running total: %d)" % (page, len(customers), koios_total))
    if len(customers) < 500:
        break
    page += 1

print("Koios: %d customers with codes" % koios_total)
print("Total SparkMeter: %d customers" % len(sm_by_code))

# ---- 1PDB ----
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT a.account_number, c.first_name, c.last_name, a.community
    FROM accounts a
    JOIN customers c ON c.id = a.customer_id
    ORDER BY a.community, a.account_number
""")
pdb_by_code = {}
for acct, fn, ln, comm in cur.fetchall():
    name = ((fn or "") + " " + (ln or "")).strip()
    pdb_by_code[acct] = {"name": name, "site": comm}

print("1PDB: %d accounts\n" % len(pdb_by_code))

# ---- Compare ----
def name_tokens(n):
    return set(n.lower().split())

all_codes = sorted(set(sm_by_code) | set(pdb_by_code))

site_stats = {}
real_mismatches = []

for code in all_codes:
    sm_name = sm_by_code.get(code)
    pdb = pdb_by_code.get(code)

    if pdb:
        site = pdb["site"]
    else:
        m = re.search(r"([A-Z]{2,4})$", code)
        site = m.group(1) if m else "UNK"

    if site not in site_stats:
        site_stats[site] = {"match": 0, "mismatch": 0, "pdb_only": 0, "sm_only": 0}

    if sm_name and pdb:
        pt = name_tokens(pdb["name"])
        tt = name_tokens(sm_name)
        overlap = len(pt & tt)
        threshold = max(1, min(len(pt), len(tt)) * 0.5)
        if overlap >= threshold:
            site_stats[site]["match"] += 1
        else:
            site_stats[site]["mismatch"] += 1
            real_mismatches.append((code, site, pdb["name"], sm_name))
    elif pdb and not sm_name:
        site_stats[site]["pdb_only"] += 1
    elif sm_name and not pdb:
        site_stats[site]["sm_only"] += 1

# ---- Report ----
print("=" * 70)
print("PORTFOLIO-WIDE DRIFT AUDIT (ThunderCloud + Koios)")
print("=" * 70)
print("\n%-6s %6s %6s %8s %8s" % ("Site", "Match", "MISMAT", "1PDB-only", "SM-only"))
print("-" * 42)
gm = gi = gp = gs = 0
for site in sorted(site_stats):
    s = site_stats[site]
    flag = " <<<" if s["mismatch"] > 0 else ""
    print("%-6s %6d %6d %8d %8d%s" % (
        site, s["match"], s["mismatch"], s["pdb_only"], s["sm_only"], flag))
    gm += s["match"]
    gi += s["mismatch"]
    gp += s["pdb_only"]
    gs += s["sm_only"]
print("-" * 42)
print("%-6s %6d %6d %8d %8d" % ("TOTAL", gm, gi, gp, gs))

if real_mismatches:
    print("\n" + "=" * 70)
    print("ALL MISMATCHES (%d)" % len(real_mismatches))
    print("=" * 70)
    cur_site = None
    for code, site, pn, sn in sorted(real_mismatches, key=lambda x: (x[1], x[0])):
        if site != cur_site:
            print("\n--- %s ---" % site)
            cur_site = site
        print("  %s: 1PDB='%s' vs SM='%s'" % (code, pn, sn))
else:
    print("\nNo real mismatches found across the portfolio.")

cur.close()
conn.close()
