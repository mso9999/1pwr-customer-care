# RCA + backfill runbook: January 2026 ThunderCloud import gap

**Status:** open as of **2026-05-02 07:30 UTC**. Affects MAK (Lesotho) historical reporting only; CC operational features and live billing are unaffected.

## Status log

| Date (UTC) | Verifier | Outcome |
|---|---|---|
| 2026-05-01 14:30 | uGridPlan agent (initial RCA) | Gap identified. Bucket-59 mean = 3.33 kWh/month, raw HH1 Jan-2026 coverage = 17.6%. Runbook drafted. |
| 2026-05-02 07:30 | uGridPlan agent (post-fix verification) | **STILL OPEN.** Re-ran the verification recipe (below) -- bucket-59 mean and raw coverage are byte-identical to 2026-05-01. |
| 2026-05-02 08:18 | CC agent (commit `b165396`, deployed) | **PARTIAL fix shipped: NOT this gap.** The dedup logic in `acdb-api/om_report.py::meter_data_export` was changed from account-level (`Set[str]`) to (account, hour)-level (`Set[Tuple[str, datetime]]`). Useful fix for accounts that have rows in BOTH `meter_readings` and `hourly_consumption` tables -- the old logic would silently hide all the latter when even one row of the former existed. **But this is a separate bug from the Jan 2026 ingestion gap.** Verification: `meter_data_export` with `customer_type=HH1, 2026-01-01..2026-01-31` returns `source_rows={meter_readings: 0, hourly_consumption: 120704}` and `raw_account_hours_covered: 0` -- the dedup set is empty, so the new code path doesn't even fire for HH1 Jan 2026. The depressed coverage (17.6%) is in `hourly_consumption` itself (rows missing from the table), not an artifact of the API filtering them out. **Original ingestion-gap fix still needed:** re-run `services/import_thundercloud.py 2026-01-01 2026-01-31` on the 1PDB host. |
| _(template -- copy into a new row when you act)_ | _(your name / agent id)_ | _(what you did + verification result)_ |

**Discovered by:** uGridPlan tenure-trend diagnostic (`scripts/diagnose_tenure_trend.py` in the `uGridPlan` repo) flagged a 10× drop in mean kWh/month at tenure-bucket 59 of `smp_hh1`. Root-cause traced to a calendar-month deficit in CC's `monthly_consumption` table for **January 2026** that propagated up through the tenure aggregation.

---

## Symptom in CC data

For HH1 customers, comparing per-meter raw readings month-over-month from the CC `meter-export` endpoint shows January 2026 has roughly half the readings per meter of the surrounding months:

| year_month | meters reporting | reads/meter | hourly coverage % |
|---|---|---|---|
| 2025-Oct | 798 | 206 | 28% |
| 2025-Nov | 863 | 253 | 35% |
| 2025-Dec | 968 | 260 | 35% |
| **2026-Jan** | **922** | **130** | **17.6%** ← deficit |
| 2026-Feb | 932 | 243 | 36% |
| 2026-Mar | 888 | 596 | 80% |
| 2026-Apr | 966 | 686 | 95% |

(Mar/Apr 2026 supra-normal coverage is unrelated — looks like a meter cadence upgrade. The deficit-only month is **2026-01**.)

The deficit is reflected in the per-meter median: Jan 2026 ≈ 1.02 kWh vs ~3 kWh in adjacent months, despite 0% zero-kW readings (so it's missing data, not zero readings).

---

## Confirmed: ThunderCloud has the data

Logged in to `https://opl-location001.sparkmeter.cloud/login` with credentials from `/opt/1pdb/.env` (`THUNDERCLOUD_USERNAME` / `THUNDERCLOUD_PASSWORD`; defaults `makhoalinyane@1pwrafrica.com` / `00001111` from `import_thundercloud.py`), then enumerated `/history/list.json`:

- All **31 daily Parquet files for January 2026 are present** at the source.
- Spot-checks of files for 2026-01-01, 2026-01-10, 2026-01-15, 2026-01-20, 2026-01-31 each show ~19,000–21,000 reading rows from 206 MAK meters (similar volume to neighbouring months).

**Conclusion:** the data exists in ThunderCloud. The gap is in our **import pipeline** between TC and 1PDB's `hourly_consumption` (and downstream `monthly_consumption`).

---

## Likely root causes (educated guesses, ranked)

1. **Daily import cron failed silently** for ~half of January 2026. The `1pdb-import.service` systemd timer is supposed to run `services/import_thundercloud.py` periodically; if it skipped a chunk of days (host reboot, disk full, transient TC 5xx, expired session), upserts wouldn't backfill missed days.
2. **Long-lived TC session expired mid-batch.** `import_thundercloud.py` does CSRF form login once and reuses the session; no token rotation. Any single import run hitting the session-timeout window would silently drop later days from that run.
3. **Schema migration / lock** on `hourly_consumption` or `monthly_consumption` during early Jan 2026 caused inserts to fail.
4. **Aggregation gap** — raw `hourly_consumption` rows might be there but the `monthly_consumption` aggregation skipped Jan 2026. Less likely (since CC's raw `meter-export` also shows the deficit) but worth ruling out.

---

## Backfill steps

Run on the host where `1pdb-import.service` lives (CC Linux host, `/opt/1pdb`):

```bash
ssh ubuntu@<EC2_LINUX_HOST>      # exact host from GitHub secret EC2_LINUX_HOST
sudo -u cc_api -H bash
cd /opt/1pdb

# 1) Confirm what we already have for Jan 2026 in hourly_consumption
psql -U cc_api -d onepower_cc -c "
  SELECT EXTRACT(DAY FROM hour) AS day, COUNT(*) AS rows, COUNT(DISTINCT meter_id) AS meters
  FROM hourly_consumption
  WHERE hour >= '2026-01-01' AND hour < '2026-02-01'
  GROUP BY day ORDER BY day;
"

# 2) Re-pull all 31 days of January 2026 from ThunderCloud
#    (import_thundercloud.py is idempotent via ON CONFLICT upserts)
source /opt/1pdb/.env
python3 services/import_thundercloud.py 2026-01-01 2026-01-31

# 3) Verify hourly_consumption now has the expected ~24*31*~206 = ~153K rows for MAK meters
psql -U cc_api -d onepower_cc -c "
  SELECT COUNT(*) AS hourly_rows,
         COUNT(DISTINCT meter_id) AS meters,
         MIN(hour) AS earliest, MAX(hour) AS latest
  FROM hourly_consumption
  WHERE hour >= '2026-01-01' AND hour < '2026-02-01';
"

# 4) Re-aggregate monthly_consumption from hourly_consumption.
#    The aggregation source-of-truth lives in 1PDB. Most likely:
bash services/sync_consumption.sh
#    OR (if the above isn't the right entrypoint, find it via:)
grep -rE 'INSERT INTO monthly_consumption|UPDATE monthly_consumption' services/

# 5) Verify monthly_consumption row for 2026-01
psql -U cc_api -d onepower_cc -c "
  SELECT COUNT(*) AS rows,
         COUNT(DISTINCT account_number) AS accts,
         SUM(kwh) AS total_kwh,
         AVG(kwh) AS avg_kwh
  FROM monthly_consumption
  WHERE year_month = '2026-01';
"
```

Acceptance criteria:

- Step 3 returns at least ~150K hourly rows from at least 200 distinct MAK meters (matching TC parquet volume).
- Step 5 average kWh per account for 2026-01 is in the 30–50 kWh range (i.e. similar to 2025-12 and 2026-02), not the depressed ~3 kWh that's currently there.

---

---

## Verification recipe (run this BEFORE marking the gap closed)

These three signals must all flip from "depressed" to "normal" before declaring victory. They can be run from any machine that can reach `https://cc.1pwrafrica.com` (no SSH or DB access needed); they hit only the public CC API.

```bash
# Setup (once per shell):
export CC_BASE=https://cc.1pwrafrica.com
export CC_EMP_ID=00   # date-based password from the CC scheme
TOKEN=$(python3 -c "
import sys, requests, datetime as dt
yyyymm = dt.datetime.now(dt.timezone.utc).strftime('%Y%m')
pw = f'{int(yyyymm)/int(yyyymm[::-1]):.10f}'.replace('.', '').lstrip('0')[:4]
r = requests.post('${CC_BASE}/api/auth/employee-login',
    json={'employee_id':'${CC_EMP_ID}','password':pw}, timeout=30)
print(r.json()['access_token'])
")
H="Authorization: Bearer $TOKEN"
```

### Signal 1 -- Tenure endpoint, bucket 59 (HH1)

```bash
curl -s -H "$H" "$CC_BASE/api/om-report/consumption-by-tenure" | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
for pt in d['chart_data']:
  if pt['tenure_month'] == 59 and pt.get('HH1') is not None:
    print(f\"bucket 59 HH1: mean={pt['HH1']:.2f}, max={pt.get('HH1_max',0):.2f}, _n={pt.get('HH1_n',0)}, _nd={pt.get('HH1_nd',0)}\")
"
```

* **Currently (gap open):** `mean=3.33, max=11.65, _n=143, _nd=99`
* **Expected post-backfill:** mean in `[20, 50]` kWh/month, max in `[80, 200]` kWh/month, `_nd` close to `_n` (~140).

### Signal 2 -- Raw meter-export coverage for January 2026

```bash
curl -s -H "$H" "$CC_BASE/api/om-report/meter-export?customer_type=HH1&start_date=2026-01-01&end_date=2026-01-31" | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
readings = d['readings']
meters = {r.get('meterid') for r in readings if r.get('meterid')}
rpm = len(readings)/max(len(meters),1)
print(f'HH1 Jan 2026: {len(meters)} meters, {len(readings):,} readings, {rpm:.0f} reads/meter, coverage = {100*rpm/(31*24):.1f}% of hourly expected')
"
```

* **Currently (gap open):** `~922 meters, ~120K readings, ~131 reads/meter, coverage = 17.6%`
* **Expected post-backfill:** coverage in `[30, 50]%` (matching surrounding months Nov 2025 / Dec 2025 / Feb 2026).

### Signal 3 -- Spot-check ThunderCloud upstream (sanity)

Confirms TC parquet files for January 2026 still exist (so the backfill HAS data to re-pull):

```bash
python3 -c "
import re, urllib3, requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
TC = 'https://opl-location001.sparkmeter.cloud'
s = requests.Session()
r = s.get(f'{TC}/login', timeout=30, verify=False)
csrf = re.search(r'name=\"csrf_token\"[^>]*value=\"([^\"]+)\"', r.text).group(1)
import os
s.post(f'{TC}/login', data={'csrf_token':csrf, 'email':os.environ.get('TC_EMAIL','makhoalinyane@1pwrafrica.com'), 'password':os.environ.get('TC_PASS','00001111')}, verify=False, allow_redirects=True)
files = s.get(f'{TC}/history/list.json', verify=False).json().get('files', [])
jan = [f for f in files if 'year=2026/month=01/' in (f.get('name') or '')]
print(f'TC parquet files for Jan 2026: {len(jan)} (expect 31)')
"
```

* **Required for backfill to be possible:** 31 daily files. If less than 31, the upstream itself has gaps and re-import alone won't help.

### One-shot verifier script

The 1PDB repo also has `services/diagnose_jan_2026_gap.py` (TODO: add this) that runs all three signals + diff against expected values, exit 0 if backfilled, 1 otherwise. Useful as a CI smoke test once landed.

---

## Post-backfill cross-repo cleanup

Once `monthly_consumption` for 2026-01 is corrected, the uGridPlan tenure NPZs need to be re-pulled to get the corrected bucket-59 values:

```bash
# In the uGridPlan repo (/Users/mattmso/Dropbox/AI Projects/uGridPlan map_v3 locally,
# or /opt/ugridplan/app on the uGridPlan host):
python3 scripts/refresh_tenure_arrays.py --sources smp_hh,smp_hh1
# Then commit + push the refreshed NPZs (will go through the build-time
# tenure-validation guard in build_acdb_cdfs.py, which now refuses to persist
# flat / non-monotonic arrays, so a successful commit also confirms the data
# quality at the bucket level).
```

---

## Prevent regression

1. **Add health-check to `1pdb-import.service`** that alerts when the most recent successful TC parquet ingestion is more than 36 hours old. Today there's no monitoring.
2. **Re-login on TC session expiry** — wrap `tc_login()` in `import_thundercloud.py` so the script re-authenticates whenever a request returns 401, instead of silently failing the rest of the batch.
3. **Daily coverage assertion** in CI — a small cron on the CC host that checks every morning whether yesterday's `hourly_consumption` row count is within 50% of the trailing 7-day median for MAK meters; alert otherwise. (Same logic surfaced this issue retroactively; running it daily would have caught Jan 2026 within 24 hours.)

---

## Diagnostic tooling references

- **uGridPlan repo:**
  - `scripts/diagnose_tenure_trend.py` — surface the bucket-59 distortion against live CC and the NPZ snapshot side-by-side.
  - `scripts/refresh_tenure_arrays.py` — surgical re-pull of `tenure_*` arrays after CC data is corrected.
  - `web/adapter/profile_8760/scripts/build_acdb_cdfs.py::_validate_tenure_arrays` — build-time guard that refuses to persist flat / non-monotonic tenure arrays.
- **1PDB repo:**
  - `services/import_thundercloud.py` — the suspected leaky pipe.
  - `services/import_tc_live.py` — separate live-consumption import path (different TC instance: `sparkcloud-u740425.sparkmeter.cloud`); not implicated in this issue but useful context.
- **1PWR CC repo:**
  - `acdb-api/om_report.py::consumption_by_tenure` — the aggregation that propagated the gap into the tenure chart.
  - `acdb-api/import_tc_transactions.py` — yet another TC pull path (transactions only, not consumption).

---

## Owner

Whoever owns `1pdb-import.service` (likely 1PDB ops, same person as `gensite-poller.md`). If unclear, escalate via the same channel as `docs/ops/rca-mak-drift-2026-04-15.md`.
