# 1PWR Customer Care - Session Log

> AI session handoffs for continuity across conversations.
> Read the last 2-3 entries at the start of each new session.

---

## Session 2026-02-15 202602151430 (Initial Setup & Financial Analytics)

### What Was Done

1. **Repository consolidation**: Moved CC system code from Email Overlord repo to dedicated `1pwr-customer-care` repo on GitHub (`mso9999/1pwr-customer-care`).

2. **Auto-deploy CI/CD**: Set up GitHub Actions workflow (`.github/workflows/deploy.yml`) with two-job architecture:
   - `deploy-frontend`: GitHub-hosted runner builds Vite frontend, rsyncs to Linux EC2 where Caddy serves static files
   - `deploy-backend`: Self-hosted Windows runner robocopy's Python backend to `C:\acdb-customer-api\`, restarts service
   - Windows runner configured as `LocalSystem` for service management permissions

3. **Financial Analytics page** (`acdb-api/frontend/src/pages/FinancialPage.tsx`):
   - Figure 1: Quarterly ARPU trend (composed bar+line chart)
   - Figure 2: Monthly ARPU trend (bars colored by quarter)
   - Figure 3: Quarterly revenue by site (stacked bars)
   - Figure 4: ARPU by site for latest quarter (bar chart + table)
   - Figure 5: Full revenue breakdown table (per-site, per-quarter)
   - PDF export for individual figures and full report

4. **ARPU calculation fixes** (3 iterations):
   - v1: Connection/termination date matching from `tblcustomer` → produced 0 active customers (broken)
   - v2: Distinct transacting accounts per period → fluctuated wildly (not representative of customer base)
   - v3 (current): **Cumulative distinct accounts** that have ever transacted up through the period → monotonically increasing, matches real customer base growth

5. **Documentation**: Comprehensive README.md, cross-linking docs between CC and uGridPlan repos.

6. **Protocol setup**: Created `.cursorrules`, `CONTEXT.md`, and `SESSION_LOG.md` for AI session continuity.

### Key Decisions
- **Two-job deploy**: Frontend on GitHub-hosted runner (Linux), backend on self-hosted Windows runner. More controllable than single-runner approach.
- **Caddy serves frontend**: Static files from Linux EC2, API proxied to Windows EC2. FastAPI does NOT serve the SPA.
- **ARPU denominator**: Cumulative distinct accounts (not per-period transacting, not connection-date based). User confirmed this matches their expectation of monotonically-increasing customer counts.
- **No staging for CC**: Single `main` branch deploys directly to production. Unlike uGridPlan which has `dev`/`main` split.

### What Next Session Should Know
- The ARPU cumulative counting approach was arrived at after two incorrect iterations. The key insight: "customers" means the total customer base (everyone who has ever purchased), not just those who transacted in a given month.
- The Windows EC2 self-hosted runner has various PowerShell quirks (robocopy exit codes, pip stderr, schtasks permissions). All documented in the deploy workflow.
- The `Email Overlord/scripts/acdb-customer-api/` folder is a legacy copy. The canonical source is now this repo.
- Caddy config is on the Linux EC2 at `/etc/caddy/Caddyfile`. If new API routes are added, they may need a new `handle` block.

### Protocol Feedback
- First session with this protocol in place for the CC repo.
- CONTEXT.md and README.md should provide sufficient orientation for future sessions.
- The `docs/whatsapp-customer-care.md` file is very comprehensive for the WhatsApp bridge subsystem.

---

## Session 2026-02-16 202602161207 (Tenure Chart Fix + CDF Library Planning)

### What Was Done
1. **Fixed Figure 6 (Consumption by Tenure)** - Multiple iterations of RCA:
   - v1: Tried joining meter table → tblaccountnumbers → tblcustomer for connection dates. Only 6 of 5023 accounts matched through tblaccountnumbers.
   - v2: Discovered `Copy Of tblmeter` has `[customer connect date]` column. Only 22 accounts had dates, and those didn't overlap with the 561 transaction history accounts.
   - v3: Tried using first-transaction-date as tenure origin with meter table customer types. Only 10 accounts overlapped because `Copy Of tblmeter` covers LEGACY pilot sites (RTE, PTA, FSI, MTK, KIT…) while `tblaccounthistory1` covers CURRENT SMP sites (MAS, MAK, KET, SHG, TLH…).
   - v4 (final): Segmented by **concession site** (last 3 chars of account number) instead of customer type, using first transaction date as tenure origin. All 561 accounts matched, 10 sites, 13 months of tenure data.

2. **Planned CDF Library enhancements** for uGridPlan (not yet executed):
   - Enhance `build_smp_cdfs.py` to save raw_8760, raw_daily, scatter, tenure arrays
   - Add missing chart implementations in `LoadProfile8760Panel.tsx`

### Key Decisions
- **Customer type not available for current SMP accounts**: `Copy Of tblmeter` (5023 rows) covers legacy pilot sites; `tblcustomer` has no customer type column. Customer types (HH, SME, etc.) simply don't exist in the database for the current 561 transacting accounts.
- **Tenure origin = first transaction date**: `DATE SERVICE CONNECTED` is only populated for 8 of 1343 customers, making it unusable as a tenure origin. First transaction date is a reliable proxy.
- **Site segmentation as alternative**: Since customer type isn't available, concession site provides meaningful geographic segmentation.

### What Next Session Should Know
- The `Copy Of tblmeter` table is a LEGACY artifact from pilot sites (Rothe, Ha Pita, Ha Fusi, Matukeng, etc.). It does NOT overlap with current SMP site data in `tblaccounthistory1`.
- Adding a `customer_type` column to `tblcustomer` would enable the originally-requested "by customer type" segmentation.
- The `tblaccountnumbers` table only has 7 rows — it's barely populated.
- The CDF library work on the `feature/8760-analysis` branch in uGridPlan is planned but not yet started.

### Protocol Feedback
- CONTEXT.md was useful for orientation but didn't document the meter table data era mismatch.
- SESSION_LOG.md continuity from prior session was helpful for understanding prior work on this feature.
- Multiple deploy cycles were needed for RCA — the backend's debug output was critical for diagnosing the data join issues remotely.

---

## Session 2026-02-16 202602162330 (ACCDB Data Sync Strategy Implementation)

### What Was Done
1. **Created `sync_accdb.ps1` (Option A)** — PowerShell script for nightly ACCDB file copy:
   - Stops the CC API service (`ACDBCustomerAPI`)
   - Copies the `.accdb` from production EC2 via SMB/UNC path
   - Removes stale `.ldb` lock files
   - Restarts the service with health check
   - Optionally re-runs `import_meter_readings.py --local-only` post-sync
   - Ready for `schtasks` registration (nightly at 2 AM)
   - Requires configuration: `$ProductionSource` must be set to the production ACCDB UNC path

2. **Extended `import_meter_readings.py` (Option B)** — Transaction/payment import:
   - New `tblmonthlytransactions` table with columns: accountnumber, meterid, yearmonth, kwh_vended, amount_lsl, txn_count, community, source
   - `fetch_koios_payments_v1()` — fetches individual payment records from `/api/v1/payments` per service area per month
   - `fetch_koios_payments_csv_amount()` — gets site-level total LSL from payments CSV
   - `import_koios_transactions_month()` — primary: v1 payments API, fallback: readings CSV + proportional LSL allocation from payments CSV
   - `import_thundercloud_transactions_month()` — extracts kWh + cost from Parquet files
   - New CLI flags: `--transactions-only`, `--no-transactions`
   - Updated `--check` mode to report tblmonthlytransactions stats

3. **Updated `om_report.py` consumption-by-tenure endpoint**:
   - Added `tblmonthlytransactions` as intermediate data source (between tblmonthlyconsumption and raw history tables)
   - Data source priority: tblmonthlyconsumption (consumption) → tblmonthlytransactions (vended, from Koios) → tblaccounthistory1/Original (vended, from ACCDB)
   - Frontend labels dynamically switch between "Consumed" and "Vended" based on data_source

### Key Decisions
- **Two-table approach**: `tblmonthlyconsumption` for actual meter readings (consumed), `tblmonthlytransactions` for payment/vending data (purchased). Different things, stored separately.
- **Koios v1 payments API primary, CSV fallback**: The v1 endpoint gives per-customer breakdown; the CSV only gives site-level aggregates. When v1 isn't available, LSL is distributed proportionally by kWh share.
- **Koios service area IDs reused from Email Overlord**: The `SERVICE_AREA_MAP` from `koios_client.py` maps to the same sites.

### What Next Session Should Know
- `sync_accdb.ps1` is NOT yet configured — user needs to provide the production EC2 hostname/IP and set `$ProductionSource`
- The Koios v1 `/api/v1/payments` response format is not publicly documented. The code dynamically tries common field names (`amount`, `energy`, `meter_serial`, etc.). First run will log the actual field names at DEBUG level.
- Both scripts are in `acdb-api/` and will auto-deploy to the Windows EC2 via the existing robocopy step in `deploy.yml`

### Protocol Feedback
- The plan file was clear about the three options and recommendation
- Conversation context from the previous session was essential for understanding the data model
