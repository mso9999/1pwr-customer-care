# 1PDB Coverage Audit Tooling

> **Purpose**: detect and surface gaps in 1PDB compared to what should be there (based on the live `meters` table and a baseline window of recent ingestion). Runs read-only on demand from the portal, persisted daily for trend visibility, and against either country DB.

This page is the operator-facing reference. For the initial findings that motivated this work, see [`coverage-audit-2026-05-02-triage.md`](coverage-audit-2026-05-02-triage.md). For the historical RCA that led to it, see [`jan-2026-thundercloud-import-gap.md`](jan-2026-thundercloud-import-gap.md).

## What it checks

| Check | What it surfaces |
|---|---|
| Per-site coverage overview | Active meter count, zero-coverage count, zero %, stale count per site |
| Per-month coverage matrix | Heatmap-style rows-and-meter-counts per (site, month) for the last N months |
| Monthly deficits | Months whose row count is below `deficit_threshold` (default 50%) of the trailing-month median for that site. **In-progress current month is reported separately, prorated by elapsed days** so day-2-of-31 isn't reported at 97% missing. |
| Last ingest per (site, source) | Most recent `reading_hour` and `created_at` per `(community, source)` pair |
| Zero-coverage active meters | `meters.status='active'` whose `account_number` has never appeared in `hourly_consumption` (joined via `account_number`, robust to the meter_id format mismatch) |
| Stale active meters | Active meters whose last reading is older than `stale_days` (default 30) |
| Cross-country leak | Meters whose `community` belongs to a different country than the DB they live in (e.g. GBO/SAM rows in `onepower_cc`) |
| Declared sites missing data | Sites in `country_config` with zero hourly rows ever |
| Orphan sites | Sites with hourly rows but no `country_config` entry |
| Koios upstream freshness | `POST /api/v2/organizations/{org}/data/freshness` per site, with delta vs our last reading. **Cached 5 min.** Helps tell apart "we missed the import" from "Koios doesn't have it yet" |

## How to use it

### From the admin UI (recommended)

`/admin/coverage` (superadmin only). The page lets you:

1. Run the live audit for **LS** or **BN** with adjustable window / stale / deficit knobs.
2. See headline totals with sparklines from the last 60 days of snapshots.
3. Drill into the per-site overview, heatmap, deficit breakdown, last-ingest trail, zero-coverage and stale meter lists, cross-country leak, declared-but-empty sites, orphans.
4. **Take a snapshot** to persist the current audit into `coverage_snapshots` (see schema in [`017..018_coverage_snapshots.sql`](../../acdb-api/migrations/018_coverage_snapshots.sql)).
5. **Probe Koios upstream freshness** to triage whether a recent gap is a missed import or upstream lag.

### From the CLI (for scripts / cron)

```bash
# On the production CC host
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; \
    /opt/cc-portal/backend/venv/bin/python3 \
    /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py \
    --country LS --window-months 8 --out /tmp/coverage-audit-LS.md'

# JSON for downstream tooling
... audit_coverage_gaps.py --country LS --json | jq '.totals'

# Persist a snapshot from the CLI (used by the systemd timer)
... audit_coverage_gaps.py --country LS --snapshot --triggered-by manual

# Locally via SSH tunnel
DATABASE_URL=postgresql://cc_api:...@localhost:5432/onepower_cc \
    python3 audit_coverage_gaps.py --country LS
```

The CLI takes the same knobs as the UI: `--window-months`, `--stale-days`, `--deficit-threshold`, `--country`, plus `--out PATH`, `--json`, and `--snapshot` for persistence.

### From the API (for automation)

| Endpoint | What it does |
|---|---|
| `GET  /api/admin/coverage/audit` | Live audit, no DB write |
| `POST /api/admin/coverage/snapshot` | Live audit + persist to `coverage_snapshots` |
| `GET  /api/admin/coverage/snapshots` | List recent snapshots (lightweight) |
| `GET  /api/admin/coverage/snapshots/{id}` | One snapshot in full |
| `GET  /api/admin/coverage/trend?country=LS&days=60` | Timeseries of headline totals for the trend chart |
| `GET  /api/admin/coverage/upstream-freshness?country=LS&refresh=false` | Probe Koios `data/freshness` per site (cached 5 min) |

All require the `superadmin` CC role.

## Daily snapshot cron

The systemd unit `cc-coverage-snapshot.timer` runs at **07:30 UTC** every day (`docs/ops/staff-pin-rotation.md` follows the same install pattern):

```bash
sudo install -m 644 -o root -g root \
    /opt/cc-portal/backend/scripts/ops/cc-coverage-snapshot.service \
    /etc/systemd/system/cc-coverage-snapshot.service
sudo install -m 644 -o root -g root \
    /opt/cc-portal/backend/scripts/ops/cc-coverage-snapshot.timer \
    /etc/systemd/system/cc-coverage-snapshot.timer
sudo systemctl daemon-reload
sudo systemctl enable --now cc-coverage-snapshot.timer
systemctl list-timers cc-coverage-snapshot.timer
```

The unit reads `/opt/1pdb/.env` for `DATABASE_URL` and `DATABASE_URL_BN` (so both countries get snapshotted in the same run). The unit's `SuccessExitStatus=0 1` means a transient failure on one country's DB doesn't fail the timer.

## How the dedup keying works (and why it matters)

The zero-coverage check joins `meters` to `hourly_consumption` via **`account_number`**, NOT `meter_id`. This is on purpose:

* `meter_readings_YYYY` carries SparkMeter serials like `SMRSD-04-0002E24D`.
* `hourly_consumption` still carries pre-migration numeric IDs (`8721`) for some rows. The April 2026 serial migration didn't fully take.

If we joined on `meter_id`, we'd report **172 of 172 KET meters as zero-coverage** even though they have 598K rows of data. Joining on `account_number` is robust to that drift.

This is the same lesson the [`jan-2026-thundercloud-import-gap.md`](jan-2026-thundercloud-import-gap.md) RCA pointed at: account-level keying is the safe granularity until the meter_id format is fully normalised.

## What to do when the audit flags something

1. **Cross-country leak**: investigate origin (probably a historical migration leak), decide migrate-or-quarantine. Owner: 1PDB ops.
2. **Historical (complete-month) deficit**: probe Koios `data/freshness` first via the UI button. If Koios has the data and we don't, re-pull via `import_hourly.py <date> <date>` (or the equivalent ThunderCloud script for MAK). If Koios doesn't have it → record as upstream gap.
3. **In-progress month deficit**: usually Koios upstream lag (check `journalctl -u 1pdb-consumption.service` for HTTP 404s). Wait 48-72h, re-run audit. Only investigate if it persists.
4. **Zero-coverage meters**: walk the list site by site. Most are typo'd account numbers at source (e.g. `0131GLBO` vs `0131GBO`), `NOMETER-*` placeholder records, or genuine orphans. Fix typos; demote orphans.
5. **Stale meters**: if cluster of stale meters share a date, look for an ingest event that day. Otherwise check meter health on-site.

## Tests

Pure-function tests in [`tests/test_coverage_audit.py`](../../acdb-api/tests/test_coverage_audit.py):

* Deficit detector handles in-progress months correctly (prorated baseline; not flagged when day-2-of-31 has expected volume; flagged when truly low).
* Deficit detector excludes the current month from the baseline used for OTHER months' detection (so an absurdly high in-progress count doesn't mask real deficits).
* Zero-coverage rollup handles unknown-active-count sites gracefully.
* Markdown rendering smoke-tests with a minimal payload.

`scripts/ops/audit_coverage_gaps.py` is exercised end-to-end in production each time the timer fires.

## Related documents

* [`coverage-audit-2026-05-02-LS.md`](coverage-audit-2026-05-02-LS.md) — first LS audit run on production
* [`coverage-audit-2026-05-02-BN.md`](coverage-audit-2026-05-02-BN.md) — first BN audit run on production
* [`coverage-audit-2026-05-02-triage.md`](coverage-audit-2026-05-02-triage.md) — initial findings + recommended ordering
* [`jan-2026-thundercloud-import-gap.md`](jan-2026-thundercloud-import-gap.md) — the dedup-bug RCA that motivated this work
