# Coverage audit triage — 2026-05-02

> Companion to [`coverage-audit-2026-05-02-LS.md`](coverage-audit-2026-05-02-LS.md) and [`coverage-audit-2026-05-02-BN.md`](coverage-audit-2026-05-02-BN.md). For each finding, this file says **what it is, who owns it, and what to do next**.

## TL;DR

The audit is now reproducible (`scripts/ops/audit_coverage_gaps.py`) and runs in ~30s against either country DB. The next step that delivers the most value is **surfacing this in CC's admin UI** so ops can self-serve the same view without SSH (built in this session as `acdb-api/coverage_audit.py` + `/admin/coverage` page). Production data backfills are deliberately deferred -- most of the recent-month deficits are caused by **Koios upstream lag** (HTTP 404s for dates < 2-7 days old), which self-heals; the older deficits need source-side investigation before any re-pull.

## Findings ranked by severity

### 1. Cross-country leak: GBO + SAM meters in the LS DB

| Foreign site | In LS DB | Note |
|---|---|---|
| GBO | 135 active meters | Last reading 2026-02-18 (74 days stale) |
| SAM | 67 active meters | Last reading 2026-02-19 |

These are Benin sites and have proper rows in `onepower_bj`. The LS-DB copies are stale shadow records, last touched on the same day (2026-02-18) -- consistent with an early multi-country migration that copied everything everywhere. **They are causing zero-coverage / stale-meter false positives in the LS audit** but no operational impact since BN serves them.

**Action**: quarantine. Either soft-flag them in LS DB (e.g., `meters.status='archived'`) or delete after taking a backup snapshot. Owner: 1PDB ops.

### 2. Historical Koios consumption gaps (real, not lag)

| Site | Month | Rows | Baseline | Missing | Notes |
|---|---|---|---|---|---|
| LSB | 2026-03 | 19 | 5,746 | **99.7%** | Likely dead month, investigate first |
| MAT | 2026-03 | 4,876 | 86,610 | 94.4% | |
| KET | 2026-03 | 2,351 | 31,932 | 92.6% | |
| MAS | 2025-12 | 3,618 | 36,487 | 90.1% | |
| MAS | 2026-01 | 3,835 | 36,487 | 89.5% | |
| KET | 2026-01 | 4,525 | 31,932 | 85.8% | |
| MAT | 2026-02 | 20,987 | 86,610 | 75.8% | |
| LSB | 2026-04 | 2,315 | 5,746 | 59.7% | |
| LSB | 2026-02 | 2,324 | 5,746 | 59.6% | |

**Action**: before backfilling, query Koios `POST /api/v2/organizations/{org}/data/freshness` for each (site, deficit_month) to confirm upstream actually has the data. If yes → re-pull via `import_hourly.py <YYYY-MM-DD> <YYYY-MM-DD>`. If no → record as upstream gap (Koios doesn't have it).

The new `acdb-api/coverage_audit.py` admin module will expose this freshness comparison directly so ops doesn't need to script it.

### 3. In-progress May 2026 deficits (probable Koios lag, will self-heal)

| Site | Rows so far | Expected so far (prorated) | Missing | Notes |
|---|---|---|---|---|
| SEH | 8 | 57 | 86.1% | Koios returning 404 since 2026-04-25 |
| SHG | 1,762 | 5,022 | 64.9% | Likely lag |
| MAS | 836 | 2,081 | 59.8% | Likely lag |

Verified from `journalctl -u 1pdb-consumption.service`: Koios is currently 404'ing many recent dates per site (5 dates for LSB, 8 for SEH, 1 for KET). This is the "1-day processing lag" documented in `CONTEXT.md`, occasionally extending to a week. **Do NOT backfill** -- check again in 48-72h.

**Action**: monitor only. The new `coverage_audit.py` upstream-freshness endpoint will make the lag visible.

### 4. 221 zero-coverage active meters (LS) -- needs decomposition

The audit currently joins via `account_number` (robust to the `meter_id` format mismatch from the April 2026 migration). Per-site distribution:

- GBO/SAM in LS DB: 41 + 9 = 50 (covered by finding #1, cross-country leak)
- SHG: 58 (15.8% of active SHG meters with no data ever)
- TLH: 24 (24% of active TLH meters)
- MAT: 22 (7.7%)
- MAS: 21 (10.3%)
- LSB: 5 (17.2%)

After dropping the cross-country leak, that's still ~170 active LS meters with no `hourly_consumption` rows. Likely causes (in decreasing probability):
1. **`account_number` was assigned but never wired to a meter at the source** (registration step incomplete). Look for null/dummy meter_ids like `ACCT-*` or `NOMETER-*` in the meters table -- both patterns appeared in the audit's first-25 sample.
2. **Account exists at the source but uses a different `account_number` string** (e.g. `0131GLBO` vs `0131GBO` -- the "L" typo in the audit's sample suggests Koios has the typo'd account but our 1PDB has the corrected version).
3. **Meter is genuinely orphan** -- decommissioned at source but kept active in our `meters` table.

**Action**: superadmin uses the new `/admin/coverage` page to walk the zero-coverage list, fix the typos, demote the orphans. Not feasible to script blind.

### 5. Orphan / pre-operational sites

* `BOB, LEB, MAN, MET, NKU, RIB, SEB, TOS` -- declared in `country_config` but no data. CONTEXT.md flags `RIB` and `TOS` as "not yet operational" -- the others probably are too. Could be marked `active=False` per-site once confirmed.
* `UNK` -- 4 active meters, 468 rows, last reading 2024-12-31. No country_config entry. **Investigate**: legacy migration leak, or test/staging data?
* `BVM, HHQ, RIP` -- single-meter sites with one zero-coverage entry each. Likely test fixtures.

**Action**: Cleanup pass. Low priority.

### 6. BN: TEST site (10 meters)

10 active meters in `onepower_bj` with site code `TEST` and account numbers like `test 3`, `test1`, `test10`. Test fixtures from earlier development. Should be deleted or marked archived.

### 7. MAK Koios source went stale 2025-07-26 (intentional)

This is **not a gap** -- TC/ThunderCloud took over as primary for MAK on that date and the Koios source was abandoned. Audit correctly flags it but it's working as intended. Note in the documentation.

## Action checklist (recommended ordering)

* [ ] Triage finding #1 (cross-country leak): decide migrate vs archive, then act
* [ ] Triage finding #6 (BN TEST meters): archive
* [ ] Triage finding #5 (orphan sites): mark non-operational sites accordingly
* [ ] Wait 72h, re-run audit. Monitor whether finding #3 (in-progress May) self-heals via Koios catch-up.
* [ ] Investigate finding #2 (historical Koios gaps): use the new `/admin/coverage/upstream` endpoint to diff against Koios `data/freshness`. Re-pull only the (site, month) cells where Koios actually has data.
* [ ] Walk finding #4 (zero-coverage meters) one site at a time using `/admin/coverage`. Fix typos, demote orphans.

## Re-running the audit

```bash
# On the production CC host
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; \
    CC_BACKEND_DIR=/opt/cc-portal/backend \
    /opt/cc-portal/backend/venv/bin/python3 \
    /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py \
    --country LS --window-months 8 \
    --out /tmp/coverage-audit-LS.md'

# Same with --country BN and DATABASE_URL=$DATABASE_URL_BN for Benin.
# Add --json for machine-readable output for downstream tooling.
```

Once the daily-snapshot timer is enabled (see `coverage_audit.py` admin endpoints), the same view is at `/admin/coverage` in the portal and `GET /api/admin/coverage/sites` programmatically.
