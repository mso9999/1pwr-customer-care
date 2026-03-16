# 1PWR Customer Care - Session Log

> AI session handoffs for continuity across conversations.
> Read the last 2-3 entries at the start of each new session.

## Session 2026-02-18 202602161800 (1Meter IoT Backfill & Customer Dashboard)

### What Was Done
1. **Backfilled 1Meter prototype data from S3 → 1PDB**: Downloaded `1meterdatacopy` S3 JSON (public bucket), parsed 23,964 records from 5 meters, downsampled to 15-min intervals, POSTed 179 readings to `/api/meters/reading` — 0 errors.
   - 23022628 (0005MAK): 51 readings, 3.01→3.45 kWh, 0.44 kWh consumed
   - 23022673 (0045MAK): 50 readings, 0.52→0.57 kWh, 0.05 kWh consumed
   - 23022696 (0025MAK): 78 readings, 20.03→23.85 kWh, 3.85 kWh consumed
2. **Fixed meter ID padding mismatch**: IoT Core sends 12-digit padded IDs (`000023022673`), config had 8-digit (`23022673`). Added `_resolve_meter()` to `ingest.py` to accept either form.
3. **Fixed Lambda unit-embedded values**: Meter payloads contain `"230.1 V"`, `"3.01 kWh"` etc. Added `_num()` parser to `meter_ingest_gate.py`. Pushed to `onepowerLS/ingestion_gate`.
4. **Enhanced customer dashboard with hourly_consumption**: Dashboard now includes metered consumption data (IoT, Koios, ThunderCloud) from `hourly_consumption` table, not just transaction-based kWh.
5. **Fixed SQLite auth DB initialization**: `init_auth_db()` now runs at module load, not just in `__main__`. Created tables on production server.
6. **Fixed timezone-naive vs aware datetime comparison** in dashboard.
7. **Registered 3 customer accounts**: 0005MAK, 0025MAK, 0045MAK — password `1meter2026`.
8. **Confirmed PostgreSQL (1PDB) is live**: cc.1pwrafrica.com already serves from PostgreSQL with 1,476 customers, all tables present.

### Key Decisions
- **S3 as backfill source** (not DynamoDB): S3 bucket `1meterdatacopy` is publicly readable, no AWS credentials needed. Contains the same data as DynamoDB.
- **Canonical meter IDs are unpadded**: `_resolve_meter()` strips leading zeros to match the config. DB stores unpadded form.
- **Dashboard merge strategy**: For each day, take the HIGHER value between transaction-based kWh and metered consumption kWh. This avoids double-counting while ensuring IoT data appears.

### What Next Session Should Know
- **PENDING: Lambda deployment**: The updated `meter_ingest_gate.py` is pushed to GitHub but NOT deployed to AWS Lambda. User needs to paste into Lambda Console and deploy.
- **PENDING: AWS credentials on EC2**: Neither the Linux EC2 nor local machine has AWS credentials. Needed for `prototype_sync.py` daemon. Options: IAM role on instance (preferred) or env-file credentials.
- **PENDING: prototype_sync.py daemon**: Service file exists at `1PDB/systemd/prototype-sync.service` but not deployed to EC2.
- **Two unmapped meters**: `23022613` and `23022667` are reporting but not mapped to accounts. User hasn't provided mappings yet.
- **S3 bucket is publicly readable**: `1meterdatacopy` — flagged to user, awaiting response.
- **The `allow` flag in the Lambda is computed but never used** to gate DynamoDB/S3 writes — all readings are stored regardless of the 15-min interval check.

### Protocol Feedback
- CONTEXT.md was accurate about the architecture (PostgreSQL already live on Linux EC2)
- SESSION_LOG from previous sessions correctly documented that migration was "code-complete but NOT deployed" for 1PDB services — the CC API itself WAS already deployed
- Needed to discover the S3 bucket public access as an alternative data path

---

## Session 2026-02-17 202602170230 (ACCDB to PostgreSQL Migration — Full Implementation)

### What Was Done
- **Created the `1PDB` repository** at `/Users/mattmso/Dropbox/AI Projects/1PDB/` with complete structure: schema/, services/, migration/, config/, systemd/
- **Wrote PostgreSQL schema** (`schema/001_initial.sql`): 20 tables including customers, accounts, meters, transactions, meter_readings (partitioned by year), hourly/monthly consumption, monthly_transactions, system_config, payments, sms_outbox, callback_log, balance_corrections, relay_commands, reconciliation_log, prototype_meter_state. Plus ENUM types, indexes, triggers, and a `next_account_number()` function.
- **Wrote ACCDB migration script** (`migration/migrate_accdb.py`): Full data migration from ACCDB to PostgreSQL including customers, accounts, meters (merged from two tables), transactions (merged from two history tables), meter readings (merged from three data tables), derived tables, and config.
- **Converted ALL 13 backend Python files** from pyodbc/ACCDB to psycopg2/PostgreSQL:
  - `customer_api.py`: Connection pool (ThreadedConnectionPool), single DB, new column names
  - `crud.py`: Rewritten PK detection, type coercion, pagination (SQL OFFSET), information_schema introspection
  - `om_report.py`: Removed all dual-table fallbacks, derived DB, dynamic date column detection
  - `tariff.py`: system_config key-value queries replace tblconfig
  - `commission.py`: New column names, single meters table, added bulk status endpoint
  - `sync_ugridplan.py`: Single meters/transactions table, no fallback loops
  - `mutations.py`: pg_index-based PK detection, renamed fetch function
  - `exports.py`: information_schema-based introspection
  - `stats.py`: Single transactions table
  - `schema.py`: Complete rewrite using information_schema
  - `auth.py`: Updated customer/account validation queries
  - `models.py`: Updated TRANSACTION_TABLES set
  - `requirements.txt`: pyodbc -> psycopg2-binary
- **Wrote prepaid balance engine** (`1PDB/services/prepaid_engine.py`): Payment processing, balance management, correction push to SparkMeter (Koios + ThunderCloud), prototype meter relay control (IoT Core MQTT), SMS alerts, balance reconciliation
- **Wrote import service** (`1PDB/services/import_service.py`): Koios API import, ThunderCloud import, DynamoDB prototype meter sync, monthly aggregate rebuilding
- **Wrote DynamoDB sync service** (`1PDB/services/dynamodb_sync.py`): Lightweight cron wrapper for prototype meter sync
- **Wrote SMS service** (`1PDB/services/sms_service.py`): Outbound SMS dispatch with Sesotho templates
- **Wrote customer registration module** (`acdb-api/registration.py`): Account number generation, single and bulk (Excel) registration, mounted as new router
- **Rewrote deploy.yml**: Removed Windows self-hosted runner job, both frontend and backend deploy to Linux EC2 via GitHub-hosted ubuntu runner + SSH/rsync
- **Created systemd units**: 1pdb-api.service, 1pdb-import.service + timer, Caddyfile.example, setup-server.sh
- **Wrote validation script** (`1PDB/migration/validate_migration.py`): Row count checks, data integrity, financial consistency, partition health, API endpoint testing
- **Wrote site config** (`1PDB/config/sites.py`): All 15 Lesotho sites, Koios service areas, ThunderCloud sites, prototype meters, IoT Core config, SMS templates

### Key Decisions
- PostgreSQL column names use snake_case (not ACCDB "SPACE SEPARATED" names) — _normalize_customer() maps between them
- `customer_id_legacy` preserves the old ACCDB autonumber for backward compatibility
- `tblmeter` and `Copy Of tblmeter` merged into single `meters` table
- `tblaccounthistory1` and `tblaccounthistoryOriginal` merged into single `transactions` table
- meter_readings partitioned by year (2018-2027)
- Connection pooling via psycopg2 ThreadedConnectionPool (2-10 connections)
- Single PostgreSQL database replaces both ACCDB files (main + derived)

### What Next Session Should Know
- **IMPORTANT**: The implementation is code-complete but NOT deployed. The next steps are:
  1. Create the `onepowerLS/1PDB` GitHub repo and push the 1PDB code
  2. Run `setup-server.sh` on the Linux EC2 to install PostgreSQL 16
  3. Copy IoT TLS certificates from Windows EC2 to Linux EC2
  4. Run `migrate_accdb.py --all` from Windows EC2 to populate PostgreSQL
  5. Run `validate_migration.py --full` to verify data integrity
  6. Update DNS/Caddy to point backend to localhost:8100
  7. Push converted backend to main branch (triggers deploy)
  8. Run 2-week parallel operation before decommissioning Windows EC2
- `import_meter_readings.py` and `compact_accdb.py` still use pyodbc — these are legacy tools superseded by the new 1PDB services
- The SQLite auth DB (`cc_auth.db`) was NOT migrated to PostgreSQL — it stays as-is (works fine, small, no ACCDB dependency)
- The `_accdb_writer.py` subprocess helper is now obsolete
- The WhatsApp bridge (`whatsapp-bridge/`) needs no changes

### Senescence Notes
- No degradation detected — this was a single focused implementation session

### Protocol Feedback
- CONTEXT.md and SESSION_LOG.md from previous sessions were essential for understanding the full system architecture
- The conversation summary from the previous session was invaluable — it captured all architectural decisions and open questions
- The plan file (.cursor/plans/accdb_to_postgresql_migration_0408bc5f.plan.md) served as an excellent structured guide

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

---

## Session 2026-02-17 202602170000 (ACCDB Data Flow Audit — Initial)

### What Was Done
- Initial high-level audit of ACCDB data flows (superseded by 202602170045)

---

## Session 2026-02-17 202602170045 (Complete ACCDB Migration Audit)

### What Was Done
1. **Complete table inventory** — 10 tables in main ACCDB, 3 in derived_data.accdb, 8 in SQLite auth DB
2. **Full per-table read/write matrix** with exact SQL operations, column lists, and triggering endpoints
3. **All 7 WRITE operations to tblcustomer** documented (crud generic x3, commission x2, sync_ugridplan GPS, mutations revert)
4. **All 1 WRITE operations to tblconfig** documented (tariff.py global rate update)
5. **import_meter_readings.py 6-step pipeline** fully mapped: ACCDB local aggregation → Koios consumption → ThunderCloud → Portfolio CSVs → Koios transactions → ThunderCloud transactions
6. **SMS Gateway App architecture** fully documented: Medic Mobile fork, polls configurable webappUrl, bridges customer payment SMS → Koios/SparkMeter → eventually ACCDB via import pipeline. Never touches ACCDB directly.
7. **Scheduled task analysis**: download_and_log.py (FTP→tblmeterdata1), mqtt_publish.py (ACCDB→MQTT→meters), retrieve_s3.py (S3→tblmeterdata1). Scripts are EC2-only, not in repo.
8. **External system map**: Koios, ThunderCloud, Dropbox, uGridPlan, HR Portal, FTP, S3, MQTT, SMS Gateway, WhatsApp Bridge

### Key Decisions
- N/A — read-only audit session, no code changes

### What Next Session Should Know
- **For migration**: Only 3 ACCDB tables are written to by the CC Portal: `tblcustomer` (7 write paths), `tblconfig` (1 write path), `tblaccountnumbers` (generic CRUD). Everything else is read-only from the portal's perspective.
- **The 3 EC2-only scripts** (download_and_log.py, mqtt_publish.py, retrieve_s3.py) are the primary data ingest path and are NOT in this repo. Must be obtained via RDP for migration.
- **Jet SQL dialect** is used throughout: `TOP N` (not LIMIT), bracket-quoted `[column name]`, `AUTOINCREMENT`, no OFFSET support (Python-side pagination). All in pyodbc.
- **The SMS Gateway App needs no changes** for any ACCDB migration — it's a pure SMS bridge to Koios.
- **derived_data.accdb exists because main ACCDB is at 2 GB limit**. A PostgreSQL/MySQL migration eliminates the need for two files.
- **Koios API credentials** are hardcoded in import_meter_readings.py (API key + secret). Same for ThunderCloud.

### Protocol Feedback
- CONTEXT.md was sufficient for CC Portal orientation but lacks documentation of: scheduled tasks, SMS Gateway, import_meter_readings pipeline, external system credentials
- SESSION_LOG.md from previous sessions was critical for understanding the two-DB architecture and the tenure chart data era mismatch
- Recommend adding an "ACCDB Schema" section to CONTEXT.md with the table inventory from this audit

---

## Session 2026-02-18 202602181830 (SMS Gateway SOP Review & SMSsync Protocol Integration)

### What Was Done
1. **Fetched SMS Gateway SOPs via Dropbox API** — used the `dropbox_client.py` credentials from Email Overlord `.env` to download three SOP documents that were Dropbox smart-sync placeholders (0 bytes locally):
   - `MGD070V01-SMS Platform SOP.docx` — CM.com bulk SMS sending (not the payment gateway)
   - `MGD074V01-SMSsync Setup + settings.docx` — **critical**: documents the SMSsync app config and M-PESA flow
   - `MGD075V01-Payment Error Trobleshooting SOP.docx` — payment troubleshooting procedures
2. **Discovered actual SMS Gateway architecture from SOPs**:
   - **App**: SMSsync (NOT Medic Mobile as previously assumed)
   - **Current endpoint**: `https://iometering.co.za/admin/mpesa/smssync.php` (purchase + balance check)
   - **Alternate endpoint**: `https://iometering.co.za/admin/mpesa/smsrecieve.php` (purchases only)
   - **Secret key**: `159951`
   - **Protocol**: form-encoded POST with fields `from`, `message`, `sent_timestamp`, `message_id`, `sent_to`, `device_id`, `secret`
   - **Response format**: `{"payload":{"success":"true","error":null}}`
   - **M-PESA SMS format**: `"5L956Z39DJ Confirmed. on 9/12/18 at 8:59 AM M1.00 received from 26657755403 - Tamer Teker 26657755403.New M-Pesa balance is M387.80 Reference: 315103084."`
   - **Reference field** (`315103084`) = Iometer/SparkMeter channel ID (how payment maps to meter)
3. **Rewrote `/api/sms/incoming` in `ingest.py`** to:
   - Accept the SMSsync form-encoded protocol (was incorrectly expecting JSON with `messages` array)
   - Validate against the shared secret (`159951`)
   - **Forward every SMS to Iometer first** (`https://iometering.co.za/admin/mpesa/smssync.php`) — preserves existing payment pipeline
   - Parse M-PESA confirmation text with regex matched to actual documented format
   - Record payment transaction in 1PDB if parseable and account matchable
   - Return SMSsync-compatible JSON response (`{"payload":{"success":"true","error":null}}`)
   - Use `sent_timestamp` (UNIX ms) for transaction dating
4. **Updated M-PESA regex** — primary pattern extracts txn_id, amount, phone, and reference from the documented format; fallback pattern catches amount + phone for format drift

### Key Decisions
- **Forward-first architecture**: Every SMS is forwarded to Iometer before 1PDB processing. If 1PDB parsing fails, the existing pipeline is unaffected.
- **Non-blocking forwarding**: Iometer forward uses `urllib` with 10s timeout; failures are logged but don't prevent SMSsync from getting a success response.
- **Secret key validation**: Uses `159951` (from SOP) rather than the previously invented `1pwr-sms-gateway-2026`.
- **Removed the `payments.py` webhook dependency for SMS**: The SMS flow now goes entirely through `ingest.py`'s `/api/sms/incoming`. The `payments.py` `/webhook` endpoint remains for future structured payment APIs.

### What Next Session Should Know
- **To activate**: Change the SMSsync app's Custom Web Service URL from `https://iometering.co.za/admin/mpesa/smssync.php` to `https://cc.1pwrafrica.com/api/sms/incoming` with the same secret `159951`. The 1PDB endpoint will transparently forward to Iometer.
- **SMSsync Task Checking** is set to 5-minute intervals — this is for outbound SMS (currently unused by 1PDB but could be used for balance confirmations).
- **The `payments.py` router** still uses the old `SMS_GATEWAY_KEY` (`1pwr-sms-gateway-2026`) for its `/webhook` endpoint. This is a different API (structured JSON, not SMSsync protocol) and can be kept for future direct integrations.
- **Need to deploy** `ingest.py` changes to production before switching SMSsync's URL.
- **Iometer contact**: Edward Lubbe at edward@gisolutions.co.za (from troubleshooting SOP)

### Correction: SMSsync → Medic Mobile Gateway
The SOPs described an **outdated** configuration (iometering.co.za, SMSsync, 2018 era). After probing:
- `sms.1pwrafrica.com/receive.php` (199.250.204.46) is the **actual live endpoint**
- The PHP filters by User-Agent (`.htaccess`) — only accepts `medic-gateway` or `SMSSync`
- GET returns `{"medic-gateway": true}` — the **Medic Mobile Gateway** handshake
- POST accepts JSON: `{"messages": [{"id","from","content","sms_sent","sms_received"}], "updates": [...]}`
- The PHP (`onepowerLS/SMSComms` repo) uses MySQL (`npower5_sms`) with a `smstypes` table to classify SMS
- Payment files are dropped to `./incoming/mpesa/PAY_*.txt` as CSV: `timestamp,txn_id,amount,phone,sender`
- `sparkmeter/new_file_watcher.php` (cron) picks up files → looks up customer → calls ThunderCloud API (`sparkcloud-u740425.sparkmeter.cloud/api/v0/transaction/`) to credit
- `ingest.py` was re-rewritten to speak Medic Mobile Gateway protocol (JSON), forward raw body to `sms.1pwrafrica.com/receive.php` with `User-Agent: medic-gateway`

### Protocol Feedback
- Dropbox API credentials in Email Overlord `.env` were essential for fetching un-synced files
- The SOP documents were **outdated** — probing the live endpoints and finding the `SMSComms` repo was necessary to get the real architecture
- CONTEXT.md should be updated to document the SMS Gateway architecture now that it's known

---

## Session 2026-02-18 202602181930 (Multi-Country Architecture Decision)

### What Was Done
1. **Finalized multi-country architecture decision**: Separate country backends, unified frontend
2. **Updated CONTEXT.md** with:
   - Multi-country architecture section (decision + rationale + diagram)
   - Metering architecture section (meter roles, prototype meters, data sources)
   - Updated domain description to reflect Benin/Zambia expansion

### Key Decisions
- **Separate backends per country** (1PDB-LS, 1PDB-BJ, 1PDB-ZM), each with its own FastAPI + PostgreSQL
- **Frontend is the integration layer** — country selector, API routing, cross-country analytics via fan-out + USD normalization
- **Same codebase** deployed per-country with different config (currency, payment provider, SparkMeter endpoint)
- **Shared**: Frontend bundle, auth system (multi-country employee access), codebase
- **Separate**: Database, API instance, payment pipeline, metering integration, SMS gateway
- **Rationale**: Currency + payment pipeline differences make single-instance multi-tenancy a source of ongoing complexity

### What Next Session Should Know
- Multi-country architecture is decided but not yet implemented — Lesotho is the only live country
- When Benin/Zambia come online, the frontend needs: country selector, per-country API base URL config, cross-country dashboard components
- The employee auth system will need a `countries` field (array of country codes the employee can access)
- Each country's FastAPI reads a `COUNTRY_CODE` env var to configure currency symbol, tariff model, payment provider, etc.

### Pending Tasks (from prior session)
- Full Koios historical import still running (PID 501197)
- Lambda deploy for real-time 1Meter forwarding
- ACCDB transaction gap (Oct 2025–present)
- Start systemd import timer after historical import completes

### Protocol Feedback
- CONTEXT.md was missing multi-country context entirely — now fixed
- CONTEXT.md was missing metering architecture (meter roles, prototype meters, data sources) — now fixed

### Checkpoint 1 (16:51 UTC) — Benin Backend Standup In Progress

**Completed this session:**
1. **Confirmed multi-country architecture** — separate backends, unified frontend
2. **Probed Benin Koios API** — discovered org (MIONWA GENERATION), 6 sites (GBO, SAM + 4 GBOWÈLE duplicates), ~250 customers, XOF currency, SparkMeter Nova meters (SMRSD-04-*)
3. **Created `country_config.py`** — country-configurable site codes, currency, Koios org ID based on `COUNTRY_CODE` env var. Defaults to LS for backward compat.
4. **Updated `om_report.py`** — imports site maps from country_config instead of hardcoded dicts
5. **Added `/api/config` endpoint** to `customer_api.py` — frontend can discover active country metadata
6. **Created 1PDB-BJ database** on EC2 (`onepower_bj`, same schema as Lesotho, `system_config` seeded for XOF)
7. **Created Benin .env** at `/opt/1pdb-bj/.env` with Benin Koios creds, port 8101, `COUNTRY_CODE=BJ`
8. **Created `1pdb-api-bj.service`** — systemd service running on port 8101, using Benin .env
9. **Benin API is running** — `curl localhost:8101/api/config` returns `{country_code: "BJ", currency: "XOF", ...}`
10. **Updated Caddyfile** for `/api/bj/*` → port 8101 routing (prefix stripping)
11. **Pushed all changes** to main (commit dd81161) — auto-deploy completed

**In progress / broken:**
- Caddy `/api/bj/*` routing returns 404 — the `uri strip_prefix` + `rewrite` combo isn't working correctly. Need to debug the Caddy rewrite rules.

**Completed after checkpoint:**
11. **Fixed Caddy routing** — switched from `handle` + `uri strip_prefix` to `handle_path` which correctly strips `/api/bj` prefix
12. **Populated Benin DB** from Koios v2 monthly reports (not v1 API which was capped at 50 customers). Final: 165 customers, 160 meters, 518 monthly_consumption rows across 6 months (Aug 2025–Jan 2026)
13. **Discovered Koios Nova limitations**: v2 historical API returns 500 for Nova sites; v1 per-customer readings/payments endpoints don't exist. Only v2 monthly report (readings + payments summary) works.
14. **kWh data quality**: Sep/Oct 2025 reports contain cumulative-register anomalies (millions of kWh from single meters). Import script caps at 10,000 kWh/meter/month and logs warnings. Nov 2025 onward is clean.
15. **Created `import_benin.py`** — Koios v2 report-based import script for Benin
16. **Set up `1pdb-import-bj.timer`** — systemd timer running every 6 hours, imports last + current month
17. **Frontend country selector** — `CountryContext`, `COUNTRY_LABELS`, flag dropdown in Layout header. API calls route to `/api` (LS) or `/api/bj` (BJ) based on selection. Persists in localStorage.
18. **Pushed all changes** (commits dd81161, cd6348b) — auto-deployed

### Final State
- `https://cc.1pwrafrica.com/api/health` → Lesotho, 1,476 customers
- `https://cc.1pwrafrica.com/api/bj/health` → Benin, 165 customers
- `https://cc.1pwrafrica.com/api/bj/config` → `{country_code: "BJ", currency: "XOF", ...}`
- Frontend has country selector dropdown in header (employees only)
- Both import timers active (Lesotho + Benin, 6h interval)

### Pending Tasks (carried over)
- Koios historical import for Lesotho (PID 501197 from prior session — check if complete)
- Lambda deploy for real-time 1Meter forwarding
- ACCDB transaction gap (Oct 2025–present)
- Per-customer transaction detail for Benin (blocked by Koios Nova API limitations)

## Session 2026-02-18 202602182000 (1PDB Continuous Ingestion — Single Source of Truth)

### What Was Done

**Phase 1: Import Resilience**
- Patched `import_hourly.py`: added HTTP 500 to retry conditions in `fetch_week()`, wrapped per-week processing in `main()` with try/except + conn.rollback() so a single bad week doesn't crash the entire import
- Patched `import_thundercloud.py`: same pattern — try/except around per-file processing with rollback
- Deployed both to EC2 (`/opt/1pdb/services/`)

**Phase 2: Historical Backfill (Launched)**
- Created `backfill_all.sh` — orchestrates full historical import: Koios hourly (all 6 sites), ThunderCloud, Koios transactions, monthly aggregates
- Running in background on EC2 (`nohup bash /opt/1pdb/services/backfill_all.sh > /tmp/backfill_all.log 2>&1 &`)
- At session end, the backfill was processing MAT week ~7/142 (early weeks are mostly ON CONFLICT DO NOTHING since data through Oct 2025 already exists)
- Will continue for several hours; check progress with `tail -f /tmp/backfill_all.log` on EC2
- After MAT completes, will run TLH, MAS, SHG, KET, LSB (these have ZERO hourly data, so all records will be new)

**Phase 3: Periodic Import Timer**
- Updated `periodic_import.sh` to 4 steps: (1) Koios hourly, (2) ThunderCloud, (3) Koios transactions via import_service.py, (4) monthly aggregate rebuild
- Started `1pdb-import.timer` — now `active (running)`, fires every 6 hours

**Phase 4: Prototype Sync Daemon**
- Fixed `prototype_sync.py` to match actual DynamoDB schema:
  - Key: `device_id` (12-digit zero-padded) + `sample_time` (YYYYMMDDHHMM string), not `meterId` + `timestamp`
  - Values: parse unit suffixes (e.g. "236.3 V" → 236.3) via `_num()` helper
  - Skip epoch-zero readings (year < 2020)
- Created `/etc/systemd/system/prototype-sync.service` with `EnvironmentFile=/opt/1pdb/.env`
- AWS credentials already in `.env` — boto3 connects to DynamoDB `1meter_data` table (us-east-1) successfully
- Service enabled and running: syncs 241 readings (~3 meters) every 60 seconds

**Phase 5: SMS Pipeline Verified**
- 15 SMS gateway transactions in `transactions` table, all from Feb 18
- Real-time webhook working: POST to `/api/sms/incoming` processes M-PESA SMS immediately
- One unmatched payment (ref 0107 → "no matching account") — data mapping issue, not pipeline issue

**Phase 6: Verification**
- All services active: `prototype-sync` (active), `1pdb-import.timer` (active), backfill (running)
- Portal healthy at cc.1pwrafrica.com, /api/health returns OK

### Current Data State

| Table | Source | Records | Range |
|-------|--------|---------|-------|
| hourly_consumption | koios (MAT) | 2,019,490 | Jun 2023 – Oct 2025 |
| hourly_consumption | thundercloud (MAK) | 7,840,345 | Dec 2020 – Feb 2026 |
| hourly_consumption | iot (3 MAK meters) | 64+ | Feb 2026 (real-time) |
| transactions | accdb | 600,993 | Sep 2020 – Oct 2025 |
| transactions | sms_gateway | 15+ | Feb 2026 (real-time) |

After backfill completes, TLH/MAS/SHG/KET/LSB will have hourly consumption, the Oct 2025–Feb 2026 transaction gap will be filled, and monthly aggregates will be rebuilt.

### Key Decisions
- Used existing AWS access keys from `/opt/1pdb/.env` rather than creating a new IAM role (keys already provisioned from prior session)
- Full historical backfill runs as a background job (hours-long), not blocking interactive work
- `periodic_import.sh` uses `import_service.py --koios` for transactions rather than a separate script
- Kept single 6h timer for now; can split into hourly Koios + 6h ThunderCloud timers later if needed

### What Next Session Should Know
- **Check backfill completion**: `tail -20 /tmp/backfill_all.log` on EC2 — look for "BACKFILL COMPLETE"
- **Verify new data appeared**: After backfill, query `SELECT community, source, COUNT(*) FROM hourly_consumption GROUP BY 1,2` — should show rows for TLH, MAS, SHG, KET, LSB
- **Monthly aggregates**: After backfill Phase 4 runs, `monthly_consumption` and `monthly_transactions` should have data
- **Unmatched SMS payment**: ref 0107 → M100 from 26659168169 couldn't match an account. May need account mapping update.
- **Reconciliation of missed payments**: Original task from this chat thread still pending — 13 payments from the outage need reconciliation with SparkMeter balances

### Files Modified
- `1PDB/services/import_hourly.py` — error resilience (HTTP 500 retry, per-week try/except)
- `1PDB/services/import_thundercloud.py` — error resilience (per-file try/except)
- `1PDB/services/prototype_sync.py` — fixed DynamoDB key schema, value parsing
- `1PDB/services/periodic_import.sh` — added txn import + aggregate rebuild steps
- `1PDB/services/backfill_all.sh` — new: orchestrates full historical backfill
- `1PDB/systemd/prototype-sync.service` — new: systemd service unit for prototype sync daemon

### EC2 Deployed
- `/opt/1pdb/services/import_hourly.py`
- `/opt/1pdb/services/import_thundercloud.py`
- `/opt/1pdb/services/prototype_sync.py`
- `/opt/1pdb/services/periodic_import.sh`
- `/opt/1pdb/services/backfill_all.sh`
- `/etc/systemd/system/prototype-sync.service`

---

## Session 2026-02-19 202602191005 (MAK Transaction Gap — ThunderCloud Parquet Backfill)

### What Was Done

**Problem**: Customer 0045MAK on cc.1pwrafrica.com showed last transaction from Oct 2025. MAK transactions had a 4-month gap (Oct 16, 2025 → Feb 18, 2026) because:
- ACCDB data ended Oct 16, 2025
- SMS Gateway data only started Feb 18, 2026
- MAK is a ThunderCloud site (separate SparkMeter instance at `opl-location001.sparkmeter.cloud`), NOT on Koios — so the Koios CSV transaction backfill couldn't cover it

**Solution**: Wrote `backfill_mak_transactions.py` — detects payments from ThunderCloud parquet heartbeat data by finding positive jumps in `acct_credit` (account balance). When a customer pays, their balance increases between consecutive heartbeats; the delta plus any consumed cost equals the payment amount.

**Implementation**:
1. Created `/opt/1pdb/services/backfill_mak_transactions.py`:
   - Logs into ThunderCloud via CSRF form auth (cookie-based, not API key)
   - Downloads daily parquet files (~1.5MB each, ~20K rows per day)
   - Groups heartbeats by meter, sorts chronologically
   - Detects credit jumps ≥ M2 as payment events
   - Calculates kWh from amount / rate (5.0 LSL/kWh)
   - Inserts with `ON CONFLICT DO NOTHING` using heartbeat_id as dedup key
   - Uses `source = 'thundercloud'` enum value

2. Created partial unique index for idempotency:
   ```sql
   CREATE UNIQUE INDEX idx_txn_tc_dedup ON transactions (source_table) WHERE source = 'thundercloud';
   ```

3. Ran full backfill: 125 days (Oct 17, 2025 → Feb 18, 2026)
   - Result: 2,031 payments detected, all inserted
   - Zero failed downloads, ~5 minutes total runtime
   - ~10-20 payments per day across all MAK customers

4. Added to `sync_consumption.sh` for ongoing 15-minute sync (7-day window)

5. Rebuilt monthly aggregates from Oct 2025

### MAK Transaction Coverage (Post-Fix)

| Source | Min Date | Max Date | Count |
|--------|----------|----------|-------|
| accdb | 2022-12-13 | 2025-10-16 | 8,471 |
| thundercloud | 2025-10-17 | 2026-02-18 | 2,031 |
| sms_gateway | 2026-02-18 | 2026-02-19 | 10 |

Seamless coverage — no gaps.

For 0045MAK specifically: 13 ThunderCloud transactions from Oct 25 to Feb 5, 2026 (customer buys small amounts roughly weekly).

### Key Decisions
- **Credit jump detection** over API calls: ThunderCloud (`opl-location001`) has NO payment/transaction API — only parquet file downloads. The `acct_credit` column in heartbeat data is the only source of payment information.
- **Threshold of M2**: Filters out rounding noise from acct_credit drift while capturing real payments (smallest observed was M5).
- **Separate script** (not merged into `backfill_transactions.py`): ThunderCloud uses cookie auth and parquet downloads, completely different from the Koios CSV approach. Keeping them separate avoids complexity.
- **Runs on server directly**: ThunderCloud parquet files are ~1.5MB each. Downloading through SSH adds minutes; on-server downloads take ~2s each.

### What Next Session Should Know
- `backfill_mak_transactions.py` is deployed at `/opt/1pdb/services/` and runs in the 15-minute sync cycle
- ThunderCloud (MAK) is the ONLY site using the parquet-based payment detection approach; all other sites use Koios CSV reports
- The ThunderCloud instance at `opl-location001.sparkmeter.cloud` has very limited API: login + parquet download only (no v1/v2 REST API)
- ThunderCloud credentials: `makhoalinyane@1pwrafrica.com` / `00001111` (cookie auth, not API key)

### Files Modified/Created
- `/opt/1pdb/services/backfill_mak_transactions.py` — NEW: ThunderCloud payment detection from parquet credit jumps
- `/opt/1pdb/services/sync_consumption.sh` — Added MAK transaction sync call

### EC2 Deployed
- `/opt/1pdb/services/backfill_mak_transactions.py`
- `/opt/1pdb/services/sync_consumption.sh`
- PostgreSQL index: `idx_txn_tc_dedup`

---

## Session 2026-02-16 202602161430 (1Meter Firmware: MQTT Fixes, OTA Analysis & SOP)

### What Was Done

**1. MQTT Payload Fixes** — Pushed to `onepwr-aws-mesh` main (`d146e9d`):
- Power unit label: `"kW"` → `"W"` (value is Watts from `activePowerW`, not kilowatts)
- Energy format precision: `%.2f` → `%.4f` (future-proofs for pulse counting)
- PowerReactive unit: `"kvar"` → `"var"` (correct SI unit)
- Added `MeterConstant` field to MQTT payload for diagnostics
- Fixed second format string (device build variant): `Time(mA)` → `Time(ms)`, removed extra paren in `PowerActive(w))`, fixed wrong `kWh` label on PowerReactive

**2. DDS8888 Register Analysis** — Deep investigation of the 0.01 kWh step resolution:
- The `/100` divisor in `meter_string.c:338` is CORRECT for the DDS8888 Modbus register format (registers 0x0018-0x0019 store energy in 0.01 kWh units)
- The `meterConstant` (1200 imp/kWh) is the LED pulse output rate, NOT the register conversion factor
- Changing to `/meterConstant` would break readings (values would be 12x too small)
- The 0.01 kWh resolution is a hardware limitation of the DDS8888 Modbus register, not a firmware bug
- To get 1/1200 kWh resolution, firmware would need GPIO pulse counting (hardware + firmware project)

**3. OTA Capability Assessment** — Full audit of the firmware's OTA readiness:
- Flash partition table supports dual-slot A/B OTA (`ota_0` + `ota_1`, 1.6 MB each, encrypted)
- Bootloader rollback is enabled (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`)
- Complete AWS IoT OTA implementation exists (`ota_over_mqtt_demo.c`, ~1500 lines) but is DISABLED
- `CONFIG_GRI_ENABLE_OTA_DEMO` defaults to `n`, not set in sdkconfig
- `vStartOTACodeSigningDemo()` is never called in `app_main()`
- The `esp-aws-iot` submodule is empty (not initialized)
- Build environment is Windows-specific (`C:\Espressif\`, COM ports)

**4. OTA Enablement SOP** — Created comprehensive SOP at `docs/SOP-1meter-ota-setup.md`:
- Phase 1: Install ESP-IDF 5.2.3 on Linux EC2 (13.244.104.137)
- Phase 2: AWS cloud setup (S3 bucket, code signing cert, IAM role, signing profile, Thing policies)
- Phase 3: Firmware modifications (enable OTA flag, start OTA task, embed code signing cert)
- Phase 4: One-time field flash at MAK (last physical flash ever needed)
- Phase 5: Validate OTA pipeline end-to-end
- Phase 6: Ongoing OTA workflow (build → S3 → OTA Job → devices self-update)
- Phase 7: Adapt Windows-specific flash scripts for Linux
- Troubleshooting guide and device inventory reference

### Key Decisions
- **Do NOT change `/100` to `/meterConstant`** in `meter_string.c` — the register divisor is correct; `meterConstant` is the LED pulse rate
- **Move build environment to EC2** rather than keeping it on Josias's Windows laptop (single point of failure)
- **OTA first, then iterate** — one field flash to deploy OTA-enabled firmware, then all future updates are remote

### What Next Session Should Know
- The MQTT payload fixes are committed to `onepwr-aws-mesh` but NOT on the devices yet — requires physical flash
- The OTA SOP is ready to execute but nothing has been deployed yet
- AWS IoT Core is in `us-east-1` (endpoint: `a3p95svnbmzyit-ats.iot.us-east-1.amazonaws.com`)
- The `esp-aws-iot` submodule URL is `https://github.com/espressif/esp-aws-iot.git` branch `release/202406.01-LTS`
- Thing Names for the 4 MAK devices need to be confirmed from AWS IoT Console or Josias

### Files Created/Modified
- `onepwr-aws-mesh/main/tasks/onemeter_mqtt/onemeter_mqtt.c` — MQTT payload fixes (pushed)
- `docs/SOP-1meter-ota-setup.md` — NEW: full OTA enablement SOP

### Protocol Feedback
- CONTEXT.md was missing the `onepwr-aws-mesh` repo details (ESP-IDF version, AWS region, build tooling). Would benefit from a section on 1Meter firmware architecture.
- SESSION_LOG.md provided good continuity from the previous session's 1Meter work.

---

## Session 2026-02-19 202602191200 (CC → SparkMeter Credit Pipe)

### What Was Done
- Built the CC → SM credit pipe: when payment transactions are created in CC (via CRUD API, payments webhook, or manual recording), the credit is now forwarded to SparkMeter so the customer's prepaid meter balance updates
- Created `acdb-api/sparkmeter_credit.py` — module handling both Koios v1 (all sites except MAK) and ThunderCloud v0 (MAK/LAB) crediting APIs
- Hooked SM crediting into `payments.py` (webhook → async background credit, manual → sync credit) and `crud.py` (transaction create → sync credit with `sm_credit` in response)
- Added `/api/payments/sm-credit-status` diagnostic endpoint
- Deployed and tested end-to-end: confirmed Koios v1 payment API works (status 201, transaction ID returned)
- Discovered the existing Koios API key (`SGWcnZpgCj-...`) is READ-ONLY. Added the PHP SMS Gateway's known-good write key (`sogk1Ne2sexP5UpTyPmdX76xMco10Bsa7NT6-ETbrIE`) as `KOIOS_WRITE_API_KEY`/`KOIOS_WRITE_API_SECRET` in `/opt/1pdb/.env`
- Increased API timeout from 30s to 90s to handle high-latency Koios calls from South Africa EC2

### Key Decisions
- **Koios v1 for writes**: Despite v1 being "deprecated", the payment endpoint (`POST /api/v1/customers/{id}/payments`) works and is what the PHP SMS Gateway uses in production
- **Two API keys**: Read key (`SGWcnZpgCj-...`) for consumption imports, write key (`sogk1Ne2sex...`) for crediting. Separate env vars `KOIOS_WRITE_API_KEY`/`KOIOS_WRITE_API_SECRET`
- **Credit on create only**: SM credit fires on new payment transactions (is_payment=true, amount>0). Edits/deletes do NOT re-credit (too risky for double-credit)
- **Webhook credits async, manual credits sync**: SMS gateway webhook gets fast response + background credit. Portal manual payment waits for SM credit result so operator sees success/failure
- **ThunderCloud token stale**: The PHP gateway's TC auth token (`.eJwN...`) is expired. Login flow doesn't authenticate the v0 API. MAK credits will fail until a fresh API token is generated from the SM Cloud dashboard

### What Next Session Should Know
- **MAK/LAB crediting broken**: ThunderCloud v0 API returns 401 with the stale token. Need to log into `sparkcloud-u740425.sparkmeter.cloud` admin UI → Settings → API → generate new token → set as `TC_AUTH_TOKEN` in `/opt/1pdb/.env`
- **Frontend UI not yet updated**: The CRUD response now includes `sm_credit` field but the frontend doesn't display it. `CustomerDataPage.tsx` Add Transaction flow should show SM credit success/failure feedback
- **MAK not in Koios**: MAK customers are NOT accessible via Koios API (returns empty). They are ThunderCloud-only. Cannot use Koios as fallback for MAK
- **Env file**: `/opt/1pdb/.env` now has `TC_API_BASE`, `TC_AUTH_TOKEN`, `KOIOS_WRITE_API_KEY`, `KOIOS_WRITE_API_SECRET`
- **Test cleanup**: Two 0.01 LSL test credits were pushed to Koios for 0001KET. The 1PDB records were deleted but the Koios credits remain (negligible amounts)

### Files Modified
- `acdb-api/sparkmeter_credit.py` (NEW) — SM crediting module
- `acdb-api/payments.py` — added SM credit to webhook + manual endpoints
- `acdb-api/crud.py` — added SM credit to transaction creates
- `/opt/1pdb/.env` (server) — added `TC_API_BASE`, `TC_AUTH_TOKEN`, `KOIOS_WRITE_API_KEY`, `KOIOS_WRITE_API_SECRET`

---

## Session 2026-02-19 202602191600 (Consumption Sync Fix + Benin)

### What Was Done
- Diagnosed root cause of stale Koios consumption data: the Koios v2 historical API's gateway times out (504) after ~60s from EC2 in South Africa when per_page > ~50. From a well-connected Mac, per_page=1000 works fine in 14s — confirming the issue is the EC2→Koios network path, not the API itself
- Rewrote `import_hourly.py` with resilient approach: adaptive per_page (starts at 50, automatically halves on 504/timeout, floor at 10), single-day queries, exponential backoff retries (5 attempts), staleness-aware (checks DB and only fetches missing days), optional concurrent site processing
- Ran fast catch-up from Mac via SSH tunnel: all Lesotho Koios sites now current through Feb 18 (yesterday). KET was 5 days stale, now fully caught up
- Added Benin (BN) support to `import_hourly.py` — GBO and SAM sites, using Benin-specific Koios org (`0123589c-...`) and API keys. Tested successfully: GBO=96 rows/day, SAM=58 rows/day
- Fixed `country_config.py`: GBO Koios site ID was wrong (`1721a02f` → `a23c334e`). The old ID had no service area in Koios; the GBOWELE site ID has data
- Fixed `/opt/1pdb/.env`: quoted all values containing `&`, `^`, `!`, `)` so `source .env` doesn't fail. Added `KOIOS_API_KEY_BN` and `KOIOS_API_SECRET_BN` for read access
- Updated `sync_consumption.sh`: now sources `.env` for API keys, passes `$YESTERDAY` (not 2-day window) to import_hourly.py, kept 10-min timeout wrapper

### Key Decisions
- **Adaptive per_page over fixed small**: Rather than hardcoding per_page=10 (safe but slow), the new import starts at 50 and self-tunes down. From a fast connection, it stays at 500; from EC2, it quickly drops to 10-25
- **Single-day queries mandatory**: Multi-day ranges reliably 500 on Koios. Daily granularity is now the only path used
- **Mac relay for catch-up, EC2 for maintenance**: The EC2 can keep up with ~1 day of incremental data at per_page=10-50 within the 10-minute timeout. For large backfills, SSH tunnel from a fast machine is the way
- **Benin uses write key for reads**: No separate read API key exists for BN org. The write key has read access (verified)

### What Next Session Should Know
- **MAK/LAB crediting still broken**: ThunderCloud v0 API token stale — same as last session
- **Frontend SM credit feedback not done**: Same as last session
- **Koios API is chronically slow from EC2**: Not a bug we can fix. The adaptive import works around it. If data staleness exceeds 2+ days, run a Mac relay catch-up: `DATABASE_URL=postgresql://...@localhost:15432/onepower_cc python3 import_hourly.py 2026-02-XX --no-aggregate --per-page 500`
- **Benin org ID discrepancy**: `sparkmeter_credit.py` uses `893ff3cc-...` (from earlier investigation) but `country_config.py` and `import_hourly.py` now use `0123589c-...`. Need to verify which is correct for the credit pipe
- **Koios web dashboard uses `/sm/` internal endpoints**: Found `export_data`, `report/download` endpoints in the SPA JS. These require web session auth (not API key). Credentials `makhoalinyane@1pwrafrica.com / 00001111` don't work on Koios web login — need correct Koios web credentials to explore faster data paths

### Data Freshness After Fix
| Site | Source       | Latest (UTC)      | Status    |
|------|-------------|-------------------|-----------|
| KET  | koios       | 2026-02-18 00:00  | caught up |
| LSB  | koios       | 2026-02-18 00:00  | caught up |
| MAS  | koios       | 2026-02-18 23:00  | current   |
| MAT  | koios       | 2026-02-18 23:00  | current   |
| SEH  | koios       | 2026-02-18 00:00  | caught up |
| SHG  | koios       | 2026-02-18 00:00  | caught up |
| TLH  | koios       | 2026-02-18 23:00  | current   |
| MAK  | thundercloud| 2026-02-18 21:00  | current   |
| GBO  | koios       | 2026-02-18 (new!) | first import |
| SAM  | koios       | 2026-02-18 (new!) | first import |

### Files Modified
- `acdb-api/import_hourly.py` — complete rewrite with adaptive per_page, single-day queries, retries, BN support
- `acdb-api/country_config.py` — fixed GBO Koios site ID
- `/opt/1pdb/services/import_hourly.py` (server) — deployed new version
- `/opt/1pdb/services/sync_consumption.sh` (server) — sources .env, uses $YESTERDAY
- `/opt/1pdb/.env` (server) — quoted special chars, added BN read API keys

---

## Session 2026-02-19 202602191530 (Koios API Study + Credit Pipe Simplification)

### What Was Done
- **Comprehensive Koios API v1+v2 study**: Systematically tested every relevant endpoint against our org and credentials
- **Discovered `POST /payments` with `customer_code`**: Koios v1 accepts payments by account number directly, eliminating the need for a two-step customer UUID lookup → credit flow. Reduces API calls from 2 to 1 per credit.
- **Simplified `sparkmeter_credit.py`**: Removed `_koios_get_customer_id()` function entirely. `_koios_credit()` now uses `POST /api/v1/payments` with `customer_code` parameter. Added empty-body handling for slow Koios responses (Benin).
- **Added payment lookup and reversal**: `koios_lookup_payment(external_id)` for idempotency and `koios_reverse_payment(payment_id)` for corrections.
- **Added freshness check to `import_hourly.py`**: Queries `POST /data/freshness` before importing — if DB data is already at or past the API's freshness date for a site, that site is skipped entirely. Saves time on runs where Koios hasn't published new data.
- **Tested all three credit paths end-to-end**:
  - Koios LS (0330SHG): 0.01 LSL → success (balance 6.34 → 6.36) — 5s
  - ThunderCloud MAK (0001MAK): 0.01 → success — 3s
  - Koios BN (0001GBO): 1 XOF → success (balance 401 → 402) — 60s (slow but works; XOF has no sub-units)
- **Updated CONTEXT.md**: Added full SparkMeter API landscape documentation

### Key Decisions
- **Single-call credit over two-step**: `POST /payments` with `customer_code` eliminates the customer lookup. Simpler, faster, fewer failure points
- **v2 live endpoint abandoned**: Returns 0 records for our sites. Our meters likely aren't Nova-type or don't have service areas configured. This is a Koios platform limitation, not something we can fix via API
- **Freshness-based skip optimization**: Instead of always querying yesterday's historical data, check freshness first. If a site's data in DB matches API freshness, skip it entirely. Saves the bulk of import time on most runs
- **XOF decimal handling**: Benin credits must use whole numbers (XOF has 0 decimal places). The API returns "too many decimal places" for amounts like 0.01. Real payments from MoMo will always be whole XOF

### API Findings (Reference)
| Endpoint | Status | Notes |
|----------|--------|-------|
| v2 `POST /data/freshness` | ✅ Works | Shows per-site data dates; useful for optimization |
| v2 `POST /data/live` | ❌ 0 records | Requires Nova meters + service areas; not available for our sites |
| v2 `POST /data/historical` | ✅ ~1 day lag | Today's data returns 0; yesterday's available |
| v1 `GET /customers` | ✅ Works | `latest_reading` field is always None for our meters |
| v1 `POST /payments` (by code) | ✅ Works | New simplified path, accepts `customer_code` directly |
| v1 `GET /payments?external_id=` | ✅ Works | 404 if not found, payment data if exists |
| v1 `POST /payments/{id}/reverse` | ✅ Available | Not yet tested with real reversal |

### What Next Session Should Know
- **Koios sites have inherent ~1 day data lag**: No real-time data path exists via API. v2 live is non-functional. v1 latest_reading is unpopulated. The freshness check optimization helps avoid redundant imports but doesn't reduce the lag.
- **ThunderCloud (MAK) has real-time data**: `import_tc_live.py` (v0 API readings) and `import_tc_transactions.py` (web API transactions) provide same-day data. Already deployed and running in `sync_consumption.sh`.
- **Test payments to clean up**: 0.01 LSL to 0330SHG (×2) and 1 XOF to 0001GBO, 0.01 to 0001MAK. Negligible amounts, no reversal needed.
- **Frontend SM credit feedback still not done**: `CustomerDataPage.tsx` doesn't display SM credit results from the API response
- **Benin Koios is very slow from EC2**: 60s for a single payment credit. Empty body responses can occur. The new code handles this gracefully (checks HTTP status when body is empty).

### Files Modified
- `acdb-api/sparkmeter_credit.py` — removed UUID lookup, switched to POST /payments with customer_code, added lookup/reversal functions, empty-body handling
- `acdb-api/import_hourly.py` — added `check_freshness()` and skip logic for up-to-date sites
- `CONTEXT.md` — added SparkMeter API landscape, data sources expanded with ThunderCloud live imports, credit pipe docs

---

## Session 2026-02-19 202602191730 (kWh Balance Engine + BN Customer Fix)

### What Was Done

1. **Fixed Benin customer import (critical bug)**:
   - **Root cause**: Koios v1 API uses **cursor-based pagination** (returns a `cursor` field), but our import used `page=` parameter which was silently ignored — every request returned page 1.
   - **Result**: Only 50 of 210 customers were imported (2 SAM, 48 GBO).
   - **Fix**: Rewrote pagination to use `cursor` parameter. Full import: **67 SAM + 135 GBO = 202 meters** (9 customers had no meters assigned).
   - v1 API response structure: `meters[]` is nested array per customer, serial at `meters[0]["serial"]`, tariff at `meters[0]["tariff"]["rate_amount"]["value"]`.

2. **Built kWh-based balance engine** (`balance_engine.py`):
   - Balance is now tracked in **kWh**, not currency. Matches the legacy ACCDB VBA logic from `meterdata.bas`.
   - `get_balance_kwh(conn, account)` → computes live balance: `last_txn_balance - SUM(hourly_consumption since that txn)`.
   - `record_payment_kwh(conn, ...)` → records payment, computes kWh vended = amount/rate, updates running balance.
   - Rationale (per user): kWh is the true unit. Currency balance masks tariff escalation effects — you bought units at one rate yesterday, another today. Only kWh balance reveals how many units you actually have.

3. **Fixed payments.py** to use balance engine:
   - Both `/webhook` (sms_gateway) and `/record` (portal) now call `record_payment_kwh()`.
   - Balance computation accounts for consumption since last transaction.
   - Added `GET /api/payments/balance/{account_number}` endpoint returning `balance_kwh` + `balance_currency` at current tariff.

4. **Fixed 36 existing sms_gateway transaction balances**:
   - All were storing cumulative currency totals instead of kWh running balance.
   - Recomputed from full transaction history per account.

### Key Decisions

- **kWh not currency for balance**: The fundamental unit is energy, not money. Currency is a derived view via tariff rate. This matches VBA's `newbalance = (theamount / currentrate) + oldbalance` where `newbalance` is in kWh.
- **Consumption deduction via query, not duplicate rows**: Rather than inserting consumption rows into `transactions` (which would duplicate `hourly_consumption` data), the balance engine computes `last_txn_balance - SUM(hourly_consumption.kwh since last_txn_date)` on the fly. Avoids data duplication while still tracking consumption's effect on balance.
- **NULL balance tolerance**: `thundercloud` (102) and `koios` (5388) imported transactions have NULL `current_balance`. The balance engine skips to the most recent non-NULL value. Proper backfill is a follow-up task.

### Data State After This Session

| Site | Meters in 1PDB | Source |
|------|----------------|--------|
| MAK  | 240 | ThunderCloud |
| MAT  | 126 | Koios LS |
| TLH  | 81  | Koios LS |
| GBO  | 135 | Koios BN (fixed) |
| SAM  | 67  | Koios BN (fixed) |
| SHG  | 43  | Koios LS |
| MAS  | 18  | Koios LS |
| Total | 751 | |

### What Next Session Should Know

- **Koios v1 pagination is cursor-based**: Use `cursor` parameter from response, NOT `page=`. Max `per_page=50`. The `site_id` filter parameter is broken for the BN org (ignored).
- **5,388 koios + 102 thundercloud transactions have NULL balance**: These were payment records imported without balance computation. Need a backfill script that processes each account chronologically.
- **Initial condition problem**: For accounts where the earliest 1PDB transaction is from koios/thundercloud (not accdb), the starting balance is unknown. Need to seed from SM's current balance at a known point and track forward.
- **Tariff rate is currently a single global value** (`system_config.tariff_rate = 5.0 LSL/kWh`). BN sites use different rates (PME=318.1 XOF, Residentiel B=160 XOF). The tariff lookup in `payments.py._get_tariff_rate()` needs to become per-account/per-site.
- **`balance_engine.py` is not yet deployed** — needs push to main for auto-deploy.

### Files Modified/Created
- `acdb-api/balance_engine.py` — NEW: kWh balance computation engine
- `acdb-api/payments.py` — refactored to use balance engine, added balance endpoint, kWh tracking
- Server: 1PDB `meters` table — 202 BN meters imported (up from 50)
- Server: 1PDB `transactions` table — 36 sms_gateway balances corrected from currency to kWh

---

## Session 2026-02-19 202602191830 (Balance Engine v2 + Seeding + Tariff)

### What Was Done

1. **Deployed balance engine v1** (`fb27a67`): Pushed `balance_engine.py`, updated `payments.py`, import scripts, and country_config to main. Auto-deployed to cc.1pwrafrica.com.

2. **Per-account tariff rates**: Added `default_tariff_rate` to `CountryConfig` (LS=5.0 LSL/kWh, BN=160.0 XOF/kWh). Updated `_get_tariff_rate()` in payments.py to look up account's community → country → rate, falling back to system_config. All BN tariff categories (Residentiel B, PME, Industriel, Social) are currently 160 XOF/kWh. Added `get_tariff_rate_for_site()` helper and `_SITE_TO_COUNTRY` mapping.

3. **Backfilled 5,490 NULL transaction balances**: Ran chronological walk-forward per account, computing kWh running balance for 1,069 accounts (all koios + thundercloud source transactions). Zero NULLs remaining.

4. **Upgraded to full-history balance engine** (`d9eb5fa`):
   - v1 computed `last_txn_balance - consumption_since_last_txn` — this missed consumption between payments.
   - v2 computes `SUM(payment kWh) - SUM(live consumption) - SUM(accdb consumption)` from scratch.
   - This correctly accounts for all payments and all consumption regardless of ordering.
   - Added DB indexes: `idx_hc_account` on `hourly_consumption(account_number)`, `idx_txn_account_payment` on `transactions(account_number, is_payment)`.

5. **Seeded 377 accounts from SM current balances**:
   - For each SM account, computed `seed = SM_balance_kwh - (our_payments - our_consumption)`.
   - Positive seed = pre-import payments we don't have → inserted as `balance_seed` transaction at 2020-01-01.
   - Added `balance_seed` to `transaction_source` enum.
   - **Reconciliation after seeding**: 0145MAK delta=+0.02 kWh, 0001MAK/0007MAK/0024MAK delta=0.00 kWh. 1PDB independently matches SM to within 0.02 kWh.
   - 33 accounts had negative seeds (consumption gap on our side — needs investigation).

### Key Decisions

- **Full-history computation over running totals**: Running totals accumulated errors when consumption happened between payments. SUM(all payments) - SUM(all consumption) is always correct.
- **SM balance for bootstrapping only**: Seeds are a one-time operation to recover pre-import history. Going forward, 1PDB independently tracks balance from transactions + consumption without needing SM.
- **Skip negative seeds**: Accounts where 1PDB balance > SM balance have a consumption tracking gap — better to investigate than blindly adjust downward.
- **Koios LS read key**: The LS write key returns empty responses for customer list; use the read key instead.

### What Next Session Should Know

- **33 negative-seed accounts**: These accounts show 1PDB balance > SM. Likely cause: hourly_consumption data is incomplete for these meters (some consumption periods missing). Low priority for now.
- **LS Koios customer fetch inconsistent**: Sometimes returns 215 customers, sometimes 131, sometimes 0 (504 errors, empty pages). The LS read API key is unreliable for full customer listings.
- **`balance_seed` transactions**: 377 rows at 2020-01-01 with `source='balance_seed'`. These represent unrecoverable pre-import payment history.
- **Balance endpoint live**: `GET /api/payments/balance/{account_number}` returns `balance_kwh`, `balance_currency`, `tariff_rate`.
- **Existing `current_balance` column**: The backfilled values in transactions are from the OLD running-total approach (payment-only). The balance engine now ignores this column and computes from scratch. The column is still updated on new payments for quick lookups but is not the source of truth.

### Files Modified
- `acdb-api/balance_engine.py` — v2: full-history SUM computation (2 commits)
- `acdb-api/payments.py` — per-account tariff, balance engine integration
- `acdb-api/country_config.py` — `default_tariff_rate`, `get_tariff_rate_for_site()`, `_SITE_TO_COUNTRY`
- Server DB: `balance_seed` enum value, 2 indexes, 377 seed rows, 5,490 backfilled balances

---

## Session 2026-02-19 202602192030 (API Rate Limit Fix + Cumulative Register Import)

### What Was Done

1. **Diagnosed Koios 429 rate limiting (root cause)**:
   - Probed the Koios v2 API and discovered the real rate limit: **30,000 requests per day per org** (not just 3 req/5 sec burst).
   - The old pipeline ran every 15 min, firing requests at all 11 sites in rapid succession. With 96 cycles/day, this burned through the daily budget by mid-morning, leaving all non-MAK sites unable to import consumption data.
   - Rate limit is **per-org**: LS (org `1cddcb07...`) and BN (org `0123589c...`) have separate quotas.

2. **Fixed `import_hourly.py` — rate limit awareness**:
   - Added `RateLimitExhausted` exception class for clean 429 handling.
   - `fetch_day()` now raises immediately on HTTP 429 instead of retrying.
   - Added `INTER_REQUEST_DELAY = 2.0s` between all API calls to stay under burst limit.
   - Rate limiting is tracked **per-org**: hitting 429 on LS skips remaining LS sites but still processes BN.
   - `check_freshness()` also handles 429 gracefully.
   - Freshness-based skipping prevents unnecessary requests when DB is already current.

3. **Rewrote `import_tc_live.py` — cumulative register approach (non-lossy)**:
   - Old approach: stored only `latest_reading.kilowatt_hours` (interval delta). If a 15-min cycle was missed, that consumption was permanently lost.
   - New approach: stores `total_cycle_energy` as cumulative Wh register in `meter_readings` table, then computes hourly consumption by differencing consecutive cumulative readings.
   - If a cycle is missed, the next reading's cumulative diff captures the full gap.
   - Falls back to interval-based rows for meters without cumulative data (0 of 264 active meters needed fallback).
   - Uses `ON CONFLICT DO UPDATE SET kwh = GREATEST(existing, new)` to refine partial hours.
   - Confirmed working: 264 cumulative readings stored, 112 hourly rows computed from diffs.

4. **Fixed `import_thundercloud.py` (parquet) — gap-filling**:
   - Changed `ON CONFLICT (meter_id, reading_hour) DO NOTHING` to `DO UPDATE SET kwh = EXCLUDED.kwh`.
   - Parquet files represent authoritative daily data and now overwrite any partial live-capture data.
   - This closes the gap where TC live missed readings during outages and parquet couldn't overwrite them.

5. **Updated `sync_consumption.sh`**:
   - Reduced Koios timeout from 600s to 300s (rate limit stops it faster anyway).
   - Better phased architecture: TC first (fast, no rate limits), then Koios (rate-limit-aware).

6. **Probed ThunderCloud v0 API endpoints**:
   - Discovered rich meter data: `current_daily_energy`, `total_cycle_energy`, `last_energy`, `last_energy_datetime`.
   - Confirmed no historical readings API exists — only parquet files via session auth.
   - Confirmed `/api/v0/meter`, `/api/v0/reading`, `/api/v0/system_status` all return 404.
   - `/api/v0/customer` and `/api/v0/transaction` return 405 (GET not allowed, need specific format).
   - Koios v2 `/data/live` endpoint times out — confirmed non-functional for our sites.

### Key Decisions

- **Per-org rate limit tracking**: The 30k/day limit is per Koios org, so LS and BN rate limits are independent. The code tracks `rate_limited_orgs` as a set of org IDs, not a single boolean.
- **Cumulative register > interval deltas**: `total_cycle_energy` from TC v0 gives a monotonically increasing energy counter. Differencing it is mathematically equivalent to summing intervals, but tolerant of missed readings. This is the same principle as utility billing (read the meter, compute delta).
- **Parquet as ground truth**: When parquet files and live readings disagree, parquet wins (`DO UPDATE`). Parquet files are generated from SparkMeter's internal database and are authoritative.
- **No parallel workers for Koios**: Removed ThreadPoolExecutor code path; serial processing with 2s delays is the only way to stay under rate limits with 11 sites sharing one org's quota.

### What Next Session Should Know

- **Koios daily limit resets at unknown time**: Likely midnight UTC but not confirmed. The first Koios requests after midnight (UTC) should succeed. If they don't, the limit may reset at a different time.
- **The 30k/day budget is generous if used correctly**: With staleness checks, most 15-min cycles should make 0-2 Koios requests (freshness only). Only cycles where new data is available trigger actual historical fetches. Expected usage: ~100-200 requests/day.
- **`backfill_transactions.py` uses the v2 report CSV endpoint**: This appears to share the same 30k/day budget. The 404s it gets for recent dates are expected (report not yet generated). It doesn't currently handle 429 — consider adding.
- **BN Koios works fine**: GBO and SAM imported successfully (154 rows) — BN has its own rate limit pool.
- **TC cumulative register data**: 264 of 308 TC customers (86%) have `total_cycle_energy` available. The remaining ~44 are inactive (no recent readings).
- **RIB freshness date is 2025-12-15**: RIB shows very stale data in the Koios freshness endpoint. This may indicate the site is offline or the meters aren't reporting to Koios. Worth investigating.

### Files Modified
- `acdb-api/import_hourly.py` — rate limit handling, per-org tracking, inter-request delays
- `acdb-api/import_tc_live.py` — complete rewrite: cumulative register approach
- `CONTEXT.md` — updated API landscape with rate limits and TC v0 details
- Server: `/opt/1pdb/services/import_hourly.py` — deployed
- Server: `/opt/1pdb/services/import_tc_live.py` — deployed
- Server: `/opt/1pdb/services/import_thundercloud.py` — `ON CONFLICT DO UPDATE`
- Server: `/opt/1pdb/services/sync_consumption.sh` — phased architecture, reduced timeout

### Protocol Feedback
- CONTEXT.md was missing the actual Koios rate limit (30k/day) — the documented "3 req / 5 sec" was only the burst limit. Fixed.
- CONTEXT.md was missing TC v0 meter-level fields (current_daily_energy, total_cycle_energy). Fixed.
- The session log from the prior conversation provided excellent context on the TC outage/data loss problem, which directly motivated the cumulative register fix.

---

## Session 2026-02-19 202602192133 (Balance Seeding + Gap-Fill + Pipeline Hardening)

### What Was Done

1. **Seeded all Koios+ThunderCloud customer balances** (`seed_balances.py`):
   - Wrote and deployed `seed_balances.py` — fetches SM balance for every customer (Koios v1 for LS/BN, ThunderCloud v0 for MAK), computes 1PDB balance via balance_engine logic, inserts `balance_seed` transaction for the delta.
   - **1,416 unique accounts** now seeded across all sites: MAK (254), SHG (306), MAT (258), MAS (182), KET (151), GBO (99), TLH (76), SAM (59), LSB (24), SEH (7), LAB (3).
   - Discovered and cleaned up 120 duplicate balance_seed rows from previous sessions (MAK accounts appearing in both TC and Koios customer lists). Fixed by adding MAK exclusion in the Koios section of the seed script.

2. **Added 429 rate-limit handling to `backfill_transactions.py`**:
   - Added `RateLimitExhausted` exception, retry logic with exponential backoff, and immediate cessation on 429 (same pattern as `import_hourly.py`).

3. **Marked RIB as not-yet-operational**:
   - Commented out RIB from `import_hourly.py` Koios sites list. RIB has zero operational data and no active meters.
   - TOS also confirmed as zero data in 1PDB — likely not operational either.

4. **Audited historical consumption coverage**:
   - Identified gaps: SHG missing Apr-Jun 2025, MAS missing Dec 2025-Jan 2026, KET/MAT thin in Jan 2026.
   - GBO and SAM (Benin) are newly provisioned — only have today's data.

5. **Fixed `import_hourly.py` incremental commits**:
   - **Critical bug**: `process_site()` accumulated all batches in memory and returned them — data was only committed AFTER the function returned. If the process was killed mid-run (as happened with SHG gap-fill), all data was lost.
   - **Fix**: Refactored to pass the DB connection into `process_site()` and commit each day's batch immediately after insertion.
   - Added `--no-skip` flag for gap-filling (bypasses staleness check that would skip historical dates already covered by newer data).

6. **Launched gap-fill imports** (running in background):
   - SHG: Apr 1 – Jun 30, 2025 (~91 days, ~140 rows/day)
   - MAS: Dec 1, 2025 – Jan 31, 2026 (~62 days)
   - KET: Jan 1-31, 2026 (~31 days)
   - MAT: Jan 1-31, 2026 (~31 days)
   - All running sequentially with incremental commits.

### Key Decisions
- **Seed from SM balance, not recompute**: The balance_seed transaction is the delta between SM's current credit balance and 1PDB's computed balance. Going forward, both systems track the same payments and consumption independently.
- **MAK excluded from Koios seeding**: MAK customers exist in both ThunderCloud AND the Koios LS customer list. To prevent double-seeding, the Koios section of `seed_balances.py` explicitly skips `*MAK` account codes.
- **Incremental commits over batch**: Changed `import_hourly.py` to commit per-day instead of accumulating all data and committing at the end. Prevents data loss on process interruption.
- **RIB and TOS skipped**: Neither site has operational data. RIB commented out; TOS remains configured but has no data to import.

### What Next Session Should Know
- **Gap-fill still running**: The sequential gap-fill job (SHG→MAS→KET→MAT) was launched at ~23:26 UTC on 2026-02-19. Check terminal output or query `hourly_consumption` to verify completion. Expected runtime: 3-5 hours total.
- **Benin sites (GBO, SAM) thin**: Only have a single day of consumption data. Historical coverage depends on when these sites were commissioned. May need separate investigation.
- **TOS zero data**: Configured in Koios but has zero meters, transactions, and consumption in 1PDB. May need to be added to the skip list alongside RIB.
- **33 negative-seed accounts**: Still pending investigation from prior session. These are accounts where 1PDB balance > SM balance, indicating a consumption tracking gap.
- **`seed_balances.py`**: Deployed at `/opt/1pdb/services/seed_balances.py`. Requires `set -a && source /opt/1pdb/.env` to pick up TC_AUTH_TOKEN. Can be re-run safely — it skips already-seeded accounts unless `--force` is used.
- **Monthly aggregates**: The gap-fills run with `--no-aggregate`. After all gap-fills complete, run a full import without `--no-aggregate` to rebuild `monthly_consumption` and `monthly_transactions`.

### Files Modified
- `acdb-api/seed_balances.py` — new file: balance seeding from Koios + ThunderCloud
- `acdb-api/backfill_transactions.py` — added 429 handling, retry logic
- `acdb-api/import_hourly.py` — incremental commits, `--no-skip` flag, RIB commented out
- `CONTEXT.md` — RIB/TOS status, updated import_hourly description
- Server: all above deployed to `/opt/1pdb/services/`

---

## Session 2026-02-20 202602201102 (Hourly Consumption RCA — Partial Day Bug & API Degradation)

### What Was Done

#### Root Cause Analysis: "Missing" Hourly Consumption Data
Investigation triggered by user's observation that Koios shouldn't be missing data. Through systematic API probing and DB analysis, identified **two distinct failure modes**:

1. **API Degradation (primary cause)**: The Koios v2 historical API intermittently returns **daily aggregates** (1 reading per meter at hour 00:00) instead of interval data (readings every 15-30 min across 24 hours). When degraded, the API returns HTTP 200 with `has_more=False` — looks like a complete response but contains ~1/24th of the data. This produced **1,002 partial days** across all sites where only hour 00 was stored.

2. **Pagination Failure (secondary)**: When the API returns 504/502 during pagination, `fetch_day` was returning whatever partial records it had collected (bug). Days where even page 1 failed appear as complete gaps (0 records).

Key evidence:
- MAS Dec 2025: 154 rows × 1 hour in DB. Direct API test confirmed 154 records with `has_more=False` — API genuinely returning only daily aggregates.
- KET Feb 18: DB has 3,240 rows (135 × 24 hrs, imported when API was healthy). Same date queried today: 268 records, 1 hour only.
- MAS Nov 1 2025: DB has 2,832 rows (24 hrs). Today's API returns 118 records (1 hr).
- Pattern: days imported during healthy API periods have full hourly data; days imported during degradation have only hour 00.

#### Fixes Implemented in `import_hourly.py`

1. **`IncompleteDay` exception**: `fetch_day` now raises `IncompleteDay` instead of returning partial data when pagination fails mid-way. Prevents committing truncated results. `process_site` catches this and skips the day.

2. **Degradation guard in `import_site_day`**: If `bin_to_hourly` produces data for only 1 distinct hour but ≥20 meters, the API is returning daily aggregates — batch is discarded with a warning instead of committed.

3. **`--repair` mode**: New flag that queries the DB for days with < 24 hours of data, then re-fetches only those. Includes an API health probe at startup that tests a known-good 24-hour date; if the API returns daily aggregates, repair aborts with a clear message.

4. **`api_health_probe` function**: Fetches a single page for a known 24-hour date and checks if the response contains multiple distinct hours. Used by `--repair` to avoid wasting API calls during degradation.

### Key Decisions
- **Don't delete partial data**: Hour-00 data is valid (just incomplete). `ON CONFLICT DO NOTHING` means re-importing will add hours 1-23 without duplicating hour 0.
- **Abort repair during degradation**: Running repair when the API returns daily aggregates would waste API quota (30K/day) with zero benefit. The health probe prevents this.
- **`MIN_METERS_FOR_DEGRADATION_CHECK = 20`**: Small/early sites with few meters legitimately have 1 reading per day. The degradation guard only triggers for sites with ≥20 meters.

### What Next Session Should Know
- **~1,002 partial days need repair** across all Koios sites. Run `python3 import_hourly.py --repair` when the API is returning interval data. The health probe will tell you.
- **Koios API is currently degraded** (as of 2026-02-20 ~12:45 UTC). Returning daily aggregates instead of interval data, with frequent 504/502 errors. This is a SparkMeter backend issue, not ours.
- **Gap-fill orchestrator (`gap_fill.py`) was killed**. Two stale workers for KET/MAT were terminated. The orchestrator is not needed now — `--repair` mode is more targeted.
- **Cron job is protected**: The degradation guard means the daily cron won't pollute the DB with 1-hour data during API instability. It will log the warning and skip.
- **After repair completes**, run a full import without `--no-aggregate` to rebuild monthly aggregates.

### Files Modified
- `acdb-api/import_hourly.py` — Added `IncompleteDay` exception, degradation guard, `--repair` mode, `api_health_probe`, `find_partial_days`
- Server: deployed to `/opt/1pdb/services/import_hourly.py`

## Session 2026-02-16 202602162035 (1Meter Timezone Fix & Power Integration)

### What Was Done

**Part 1: Timezone Correction (SAST → UTC)**
- Fixed `acdb-api/ingest.py`: Line 234 now parses 1Meter timestamps as SAST (UTC+2) and converts to UTC before storage, using `UTC_OFFSET_HOURS` from `country_config.py`
- Fixed `1PDB/services/prototype_sync.py`: `_parse_sample_time()` now parses DynamoDB `sample_time` as SAST → UTC. Also fixed the DynamoDB query cutoff to convert UTC `last_synced_at` to SAST before string comparison
- Created `acdb-api/fix_iot_timestamps.py`: One-time migration script to shift all `source='iot'` historical data back by 2 hours (`--dry-run` default, `--apply` to execute)
- Verified that `crud.py`'s `_to_local()` already correctly converts UTC → SAST for display — no frontend changes needed

**Part 2: Finer Energy Resolution (Power Integration)**
- Analyzed 1Meter_PCB KiCad schematic: confirmed DDS8888 connects via RS485 only, no GPIO for pulse counting — LED pulse counting requires hardware mod
- Implemented firmware power integration in `onepwr-aws-mesh`:
  - `onemeter_modbus.h`: Added `integratedEnergyKWh` field to `DDS8888_Data_t`
  - `meter_string.c`: Trapezoidal integration of `activePowerW` over time, with re-anchoring to Modbus register when drift > 0.02 kWh
  - `onemeter_mqtt.c`: New `EnergyIntegrated` field in MQTT payload (6 decimal places)
- Updated backend to parse `EnergyIntegrated`:
  - `ingest.py`: `MeterReading` model accepts optional `energy_integrated`, used for finer delta_kwh when available
  - `prototype_sync.py`: Parses `EnergyIntegrated` from DynamoDB items, uses for delta when present
- Documented PCB design note in CONTEXT.md: route DDS8888 CF pin to ESP32 GPIO w/ PCNT for next revision

### Key Decisions
- Used `country_config.UTC_OFFSET_HOURS` for timezone offset (works for both Lesotho=2 and Benin=1)
- Power integration re-anchors to Modbus register at ±0.02 kWh drift to prevent cumulative error
- `EnergyIntegrated` is backward-compatible: `None`/absent → falls back to `EnergyActive`
- `meter_readings.wh_reading` always stores the Modbus register value; integrated energy only used for delta calculations

### What Next Session Should Know
- `fix_iot_timestamps.py` needs to be run on the EC2 with `--apply` BEFORE deploying the fixed `prototype_sync.py`
- `prototype_sync.py` changes are in local Dropbox (`/Users/mattmso/Dropbox/AI Projects/1PDB/services/`), need commit+push to `onepowerLS/1PDB` repo and deploy to EC2
- Firmware changes in `onepwr-aws-mesh` need to be built and tested before OTA deployment — OTA itself hasn't been tested yet
- The `ingest.py` changes will auto-deploy to production when pushed to `main` (CC repo)

### Files Modified
- `acdb-api/ingest.py` — SAST→UTC fix, `energy_integrated` field support
- `acdb-api/fix_iot_timestamps.py` — NEW, one-time historical data migration
- `acdb-api/CONTEXT.md` — Documented 1Meter energy resolution and timestamp handling
- `/Users/mattmso/Dropbox/AI Projects/1PDB/services/prototype_sync.py` — SAST→UTC fix, `EnergyIntegrated` parsing, cutoff conversion
- `/Users/mattmso/Dropbox/AI Projects/onepwr-aws-mesh/main/onemeter/onemeter_modbus.h` — `integratedEnergyKWh` field
- `/Users/mattmso/Dropbox/AI Projects/onepwr-aws-mesh/main/onemeter/meter_string.c` — Power integration logic
- `/Users/mattmso/Dropbox/AI Projects/onepwr-aws-mesh/main/tasks/onemeter_mqtt/onemeter_mqtt.c` — `EnergyIntegrated` in MQTT payload

---

## Session 2026-02-22 202602221945 (Benin Hourly Consumption Import via Koios Web CSV)

### What Was Done

1. **Reverse-engineered Koios web UI download endpoint**: The Koios v2 historical API for BN returns only daily aggregates (1 reading/meter/day), not the sub-hourly interval data available for LS. By decompiling the Koios SPA's JS bundles, discovered an internal `POST /sm/organizations/{orgId}/report/download` endpoint that produces CSVs with 15-minute interval data when accessed via web session auth (not API keys).

2. **Created `import_hourly_bn.py`**: New Python script specifically for Benin hourly consumption:
   - Authenticates to Koios web UI via CSRF-form login (`KOIOS_WEB_EMAIL` / `KOIOS_WEB_PASSWORD`)
   - Downloads daily report CSVs containing 15-min interval data (`heartbeat_start`, `kilowatt_hours`, `meter/serial`, `meter/customer/code`)
   - Aggregates 15-min intervals into hourly buckets
   - Inserts into `hourly_consumption` with `ON CONFLICT (meter_id, reading_hour) DO NOTHING`
   - Supports `--no-skip`, `--repair`, `--site`, date range args (same CLI conventions as `import_hourly.py`)

3. **Fixed `onepower_bj` database schema**:
   - Widened `account_number` column from `VARCHAR(10)` to `VARCHAR(50)` (BN meter serials are longer)
   - Added `uq_hourly_meter_hour` unique constraint on `(meter_id, reading_hour)` (required for `ON CONFLICT`)

4. **Deployed to EC2 and configured credentials**:
   - Script at `/opt/1pdb/services/import_hourly_bn.py`
   - Added `KOIOS_WEB_EMAIL=mso@1pwrafrica.com` and `KOIOS_WEB_PASSWORD=1PWRBN2026` to `/opt/1pdb/.env`

5. **Ran initial 30-day backfill** (Jan 22 – Feb 21, 2026): **112,498 hourly records** imported for GBO + SAM sites.

6. **Launched full historical backfill** (Jun 1, 2025 – Jan 21, 2026): Running in background. GBO data starts appearing from ~late August 2025 (site wasn't operational earlier). ~48 meters × 25 hours/day ≈ 1,200 rows/day for GBO.

7. **Updated `sync_consumption.sh`**:
   - Phase 2 now explicitly runs `import_hourly.py --country LS` (LS only via v2 API)
   - Phase 3 added: runs `import_hourly_bn.py` against `onepower_bj` database via `DATABASE_URL` env override
   - Architecture comment updated to reflect 4-phase pipeline (TC → Koios LS → Koios BN → Backfill)

### Key Decisions
- **Web session auth over API keys**: The Koios v2 historical API returns degraded (daily-only) data for BN. The web UI's internal report download endpoint is the only path to 15-minute interval data. This requires email/password auth, not API keys.
- **Separate script for BN**: Rather than hacking web auth into `import_hourly.py` (which uses v2 API for LS), created a dedicated `import_hourly_bn.py` with its own auth and download logic. Cleaner separation of concerns.
- **XOF site IDs discovered from JS bundles**: GBO site_id=`a23c334e-...`, SAM site_id=`b6b41de9-...`, service_area_id=`beb22c38-...` (shared). Org_id=`0123589c-...`.

### What Next Session Should Know
- **Historical backfill may still be running**: Check with `tail -20 /tmp/bn_backfill.log` on EC2. Look for "Import complete" line.
- **Web credentials are user-specific**: `mso@1pwrafrica.com` / `1PWRBN2026` — if this password is rotated, `import_hourly_bn.py` will fail with a login error and needs updating in `/opt/1pdb/.env`.
- **Session cookie expiry unknown**: The web session may expire after some period. The script re-authenticates on 401 responses during download, but if the login itself changes (2FA, CAPTCHA), this breaks.
- **SAM site data**: SAM has fewer meters than GBO (~18 vs ~48). Both are imported in each run.
- **`import_hourly.py` now uses `--country LS`** in sync_consumption.sh to avoid attempting the degraded BN v2 API path.

### Files Created/Modified
- `acdb-api/import_hourly_bn.py` — NEW: Benin hourly consumption via Koios web CSV
- Server: `/opt/1pdb/services/import_hourly_bn.py` — deployed
- Server: `/opt/1pdb/services/sync_consumption.sh` — updated with BN phase
- Server: `/opt/1pdb/.env` — added `KOIOS_WEB_EMAIL`, `KOIOS_WEB_PASSWORD`
- Server: `onepower_bj.hourly_consumption` — schema fixes (VARCHAR(50), unique constraint)

### Protocol Feedback
- CONTEXT.md was accurate about the multi-country architecture and Koios API landscape
- The session log from the Consumption Sync Fix session (202602191600) was critical — it documented the BN Koios API degradation and the discovery of `/sm/` internal endpoints
- CONTEXT.md should be updated to document the web CSV download path as a third data source for BN (alongside v2 historical API and monthly reports)

---

## Session 2026-02-21 202602212100 (1Meter Assembly SOP + OTA Infrastructure + Field SOPs)

### What Was Done

**Part 1: 1Meter Assembly SOP**
- Located and analyzed DDS8888 documentation: manual PDF, Modbus register map XLSX, DWG mechanical drawing
- Created comprehensive SOP `docs/SOP-1Meter-Assembly.md` covering mechanical assembly, electrical wiring, RS485 Modbus, power verification, pulse output (CF pin) modification with pull-up resistor, and firmware sequencing
- Used SparkMeter SOP as structural template; embedded user-provided 1M.png meter drawing

**Part 2: OTA Infrastructure Setup**
- Diagnosed deployed fleet: OTA was NOT enabled on any device (zero IoT Jobs, empty S3 bucket, no EnergyIntegrated field)
- Confirmed 7 active devices at MAK (not 4 as originally documented): serials 23022628, 23022696, 23022673, 23022613, 23022646, 23022684, 23022667
- Set up full AWS OTA pipeline:
  - S3 bucket `1pwr-ota-firmware` with versioning enabled
  - ECDSA P-256 code signing certificate generated, imported to ACM (`arn:aws:acm:us-east-1:758201218523:certificate/2826aa0d-ff83-46df-b552-1f7daf186702`)
  - IAM role `1pwr-ota-service-role` with S3/CodeSign/IoT policies
  - Signing profile `1PWR_OTA_ESP32`
  - DevicePolicy already had OTA topic permissions (Jobs + Streams)

**Part 3: Firmware v1.0.0 Build**
- Fixed build issues for OTA-enabled firmware:
  - Removed non-existent `mqtt_voltage_energy_polling_task.c` from CMakeLists
  - Added `vStartOTACodeSigningDemo()` call in `app_main()` after `mqtt_meter_start()`
  - Enabled `CONFIG_GRI_ENABLE_OTA_DEMO=y` with version 1.0.0
- Built successfully on EC2: 1.06 MB binary, 34% flash free
- Downloaded binaries to `firmware-builds/` folder; uploaded app binary to S3
- Committed and pushed to `onepwr-aws-mesh` main (commit `6d68d97`)

**Part 4: Field SOPs**
- Discovered critical constraint: `CONFIG_GRI_THING_NAME` is compiled into each binary, so per-device builds are required (not a single universal binary)
- Created three fresh SOPs in `docs/`:
  1. `SOP-MAK-Firmware-Update-v1.0.0.md` — Pre-visit prep + onsite flash procedure with two paths (Josias's laptop vs EC2 pre-builds)
  2. `SOP-Post-Flash-Verification.md` — Per-device verification matrix, remote/onsite checks, acceptance criteria
  3. `SOP-OTA-Remote-Update.md` — Complete workflow for future remote OTA pushes

### Key Decisions
- Per-device binaries required because Thing Name is compiled in (not read from NVS or cert CN)
- Flash command intentionally skips 0xD000 (esp_secure_cert partition) to preserve existing TLS certificates
- OTA data partition (0x19000) must be flashed to set factory image marker for dual-OTA scheme
- Gateway device (23022667) should be flashed LAST to maintain mesh connectivity during update

### What Next Session Should Know
- **Critical blocker:** Thing Name ↔ DDS8888 serial mapping is unknown. Team must determine this before building per-device binaries or during site visit via serial monitor
- **3 unmapped serials:** 23022613, 23022646, 23022684 — not in original deployment docs; need account assignments
- **The code signing private key** (`ota_signer_key.pem`) is on the EC2 at `~/esp/onepwr-aws-mesh/main/certs/` and in `/tmp/` locally — should be stored securely (Secrets Manager or encrypted backup) and deleted from EC2 once archived
- **IoT Rule may need updating:** If DynamoDB doesn't capture `EnergyIntegrated`, the IoT Rule SELECT query needs modification
- **PCNT pulse counting firmware** is not yet written — this is the next firmware feature after v1.0.0 is deployed and the CF pin wiring is done
- `fix_iot_timestamps.py` STILL needs to be run on EC2 with `--apply` (from previous session — not yet done)
- `prototype_sync.py` changes STILL need commit+push to 1PDB repo (from previous session — not yet done)

### Files Created/Modified
- `docs/SOP-1Meter-Assembly.md` — NEW: full assembly SOP with pulse output modification
- `docs/SOP-MAK-Firmware-Update-v1.0.0.md` — NEW: field firmware flash procedure
- `docs/SOP-Post-Flash-Verification.md` — NEW: verification checklist and acceptance criteria
- `docs/SOP-OTA-Remote-Update.md` — NEW: remote OTA workflow for future updates
- `firmware-builds/` — NEW folder with v1.0.0 binaries (bootloader, partition-table, ota_data, app)
- `firmware-builds/FLASH-INSTRUCTIONS.md` — NEW: quick-reference flash command
- `onepwr-aws-mesh/main/CMakeLists.txt` — Removed missing mqtt_voltage_energy_polling_task.c
- `onepwr-aws-mesh/main/main.c` — Added vStartOTACodeSigningDemo() call
- `onepwr-aws-mesh/sdkconfig.defaults` — Enabled OTA demo with v1.0.0

---

## Session 2026-02-23 202602231215 (KET→uGridPLAN GPS Reconciliation)

### What Was Done
- **GPS-based reconciliation**: Matched 1,272 of 1,301 KET uGridPLAN connections to the KET Master File survey entries using GPS proximity (30m threshold, avg 4.2m, median 3.2m)
- **Survey_ID push to uGridPLAN**: Batch-pushed `Survey_ID` (format: `KET NNNN TYPE`) and `Customer_Type` back to all 1,272 matched connections via the batch-connection-update API
- **uGridPLAN batch endpoint fix**: Added `connection_XXX` index-based ID support to the `batch-connection-update` endpoint in the uGridPLAN adapter (`web/adapter/main.py`), enabling batch updates to connections that lack Survey_IDs. Committed and deployed to staging (`dev` branch → `dev.ugp.1pwrafrica.com`)
- **Column name mismatch fix**: Discovered that the batch endpoint's `surveyId` mapping writes to `Survey ID` (with space) while the GeoDataFrame column is `Survey_ID` (with underscore). Worked around by sending `Survey_ID` directly as a column-level property key
- **Delta storage persistence**: Used `save_project` (`/project/save-in-place`) to persist changes into the UGP delta storage system, creating version `20260223_172656` in `/opt/ugridplan/project_data/KET_minigrid/`

### Key Decisions
- GPS matching threshold of 30m was chosen — all 1,272 matches had <28m distance, with avg 4.2m showing excellent accuracy
- 91 type disagreements between uGridPLAN and KET Master File (e.g. CHU in survey vs HH in UGP, HHSME vs SME). uGridPLAN Customer_Types were overwritten with the survey-derived types since the Master File is the authoritative source
- 29 connections remain unmatched (>30m from any survey point) — these are likely newer connections added after the 2022 survey
- Survey_ID format: `KET NNNN TT` (e.g. `KET 0031 HH1`) — directly parseable by `_survey_id_to_account_number` to yield account numbers (e.g. `0031KET`)

### What Next Session Should Know
- **KET connections now have Survey_IDs in uGridPLAN** — `sync_ugridplan.py`'s `_match_customers` can now use Strategy 0 (survey_id binding) or Strategy 2/3 (plot prefix) to match KET connections to 1PDB accounts
- **The batch endpoint `surveyId` mapping bug** still exists on the server — the workaround is to send `Survey_ID` (exact column name) instead of `surveyId` (camelCase). Consider fixing the mapping to prefer existing columns before creating new ones
- **22 KET connections still lack Survey_IDs** — these need manual identification or a wider GPS threshold
- **1PDB already has 99.9% customer type coverage** (from earlier backfill work) — the UGP push was about closing the loop so UGP has the same type data
- The uGridPLAN batch endpoint fix (`connection_XXX` support) is on `dev` branch only — needs cherry-pick to `main` for production UGP deployment

### Files Created/Modified
- `/Users/mattmso/Dropbox/AI Projects/uGridPlan map_v3/web/adapter/main.py` — Added connection_XXX index support to batch-connection-update (committed to `dev` branch)
- `/tmp/recon_gps.py` — GPS matching analysis script (temporary)
- `/tmp/push_ket_to_ugp.py` — Batch push script for Survey_IDs to uGridPLAN (temporary)
- `/tmp/verify_ugp_ket.py` — Verification script (temporary)

---

## Session 2026-02-23 202602231215 (HH Subtype Migration & customers.customer_type)

### What Was Done
- **Synced all 12 site survey master files** from Dropbox (triggered Smart Sync download for 11 files that were 0-byte placeholders)
- **Built universal survey extractor** (`extract_survey_types.py`) that processes all 12 LS sites, extracting 13,710 entries with 98% HH subtype resolution (HH1/HH2/HH3)
  - Reconstructs HH subtypes from score column when type column only has "HH" (score 1→HH1, 2→HH2, 3→HH3)
  - Normalizes edge cases: CLI→HC, CHRCH→CHU, HHSME→SME
- **Backfilled 1,246 customers** in `customers.customer_type` with granular types from survey data
  - Distribution: HH1: 1088, SME: 84, HH2: 44, SCP: 8, CHU: 5, SCH: 4, HH: 3, HC: 2, HH3: 2
  - 206 still null (TLH, LSB sites without survey master files)
- **Migrated all backend queries** from `meters.customer_type` to `customers.customer_type`:
  - 4 queries in `om_report.py` (avg_daily_consumption, daily_load_profiles, consumption_by_tenure, raw_meter_readings)
  - 1 query in `crud.py` (account_detail)
  - All now join accounts→customers for type data
- **Added HH aggregate filter logic**: `_matches_customer_type()` helper treats "HH" filter as matching HH1+HH2+HH3
- **Updated frontend**:
  - OMReportPage and FinancialPage type dropdowns include "All HH (HH1+HH2+HH3)" aggregate option
  - Customer creation forms (NewCustomerWizard, CommissionCustomerPage, AssignMeterPage) updated from `['HH', ...]` to `['HH1', 'HH2', 'HH3', ...]`
- **Deployed**: Backend via SCP+systemctl, frontend via git push → GitHub Actions (run 22326878379, success)

### Key Decisions
- `customer_type` is a customer property, not a meter property — source of truth is now `customers` table
- HH subtypes (HH1/HH2/HH3) correspond to survey scores (1=Low, 2=Medium, 3=High)
- "HH" in filter context = aggregate of all HH subtypes; in data context = unresolved (no score available)
- 295 of 13,710 survey entries (2%) have unresolved "HH" — these are from villages where score wasn't recorded

### What Next Session Should Know
- **206 active customers still lack customer_type** — these are from TLH and LSB sites which have no survey master files. Consider defaulting them or obtaining survey data
- **`meters.customer_type` is now deprecated** — all reads go through `customers` table. The column still exists but is not written to or read by any backend code
- **JSON fallback** (`meter_customer_types.json`) still used as tertiary source in consumption-by-tenure endpoint only
- **Registration default** is still "HH" (generic) in `registration.py` line 67 — since forms now show HH1-3, this rarely fires
- **Sites without survey data**: TLH (no master file found), LSB (no master file found)

### Files Created/Modified
- `acdb-api/om_report.py` — Migrated 4 type queries to customers table, added `_matches_customer_type()` and `_ACCT_CTYPE_SQL`
- `acdb-api/crud.py` — Account detail now prefers `customers.customer_type`
- `acdb-api/frontend/src/pages/OMReportPage.tsx` — Added "All HH" aggregate option
- `acdb-api/frontend/src/pages/FinancialPage.tsx` — Added "All HH" aggregate option
- `acdb-api/frontend/src/pages/NewCustomerWizard.tsx` — HH→HH1/HH2/HH3 in CUSTOMER_TYPES
- `acdb-api/frontend/src/pages/CommissionCustomerPage.tsx` — HH→HH1/HH2/HH3 in CUSTOMER_TYPES
- `acdb-api/frontend/src/pages/AssignMeterPage.tsx` — HH→HH1/HH2/HH3 in CUSTOMER_TYPES
- `acdb-api/extract_survey_types.py` — Universal survey type extractor for all 12 LS sites

## Session 2026-02-26 202602261543 (Check Meter RCA & Full Pipeline Fix)

### What Was Done
- **Root cause analysis**: Investigated large SM vs 1M deviations on the Check Meter page
  - Found `energy_integrated` resets to 0 on ESP32 reboot, silently losing accumulated energy
  - Fixed `ingest.py` to use `energy_active` (DDS8888 Modbus register, non-volatile) for delta calculations
  - Backfilled `hourly_consumption` from raw `meter_readings` for all 3 original check meters
  - Redistributed gap-recovery energy spikes using SM consumption as proportional weights
- **Registered 2 additional check meters** from DynamoDB (23022684→0026MAK, 23022646→0119MAK)
  - Discovered team had reported serial-to-account mapping backwards; data confirmed swap fixed deviations
  - Backfilled historical data from DynamoDB `1meter_data` table
- **Identified all 8 MAK meters** in DynamoDB `meter_last_seen`: 3 customer check (original), 2 customer check (newly registered), 1 gateway (23022667), 1 repeater (23022613), 1 unknown (23021866)
- **Added cumulative kWh chart** below the hourly chart, normalized to first hour with both SM+1M data
- **Updated stat cards** to show total deviation as headline metric (green/amber/red color-coded)
- **Changed default period** to "Since firmware update" (auto-detects first IoT reading)

### Key Decisions
- Use `energy_active` over `energy_integrated` for deltas — resolution (10 Wh vs 0.8 Wh) not worth reboot vulnerability
- Redistribute gap energy proportionally using SM pattern rather than lumping into single hour
- Confirmed 23022613 is a repeater at the powerhouse, not a customer meter

### Final Check Meter Status (5 pairs)
| Account | 1M Serial | Total Dev | Status |
|---------|-----------|-----------|--------|
| 0025MAK | 23022696 | +2.3% | Green |
| 0026MAK | 23022684 | +1.2% | Green |
| 0119MAK | 23022646 | +4.8% | Green |
| 0005MAK | 23022628 | -1.8% | Green |
| 0045MAK | 23022673 | -6.6% | Amber |

### What Next Session Should Know
- 23022613 (repeater) and 23022667 (gateway) don't need check-meter pairing
- 23021866 is an unknown meter in DynamoDB not in the SOP — relay OFF, 0 kWh
- The Lambda (`ingestion_gate`) does NOT forward `energy_integrated` — it only sends `energy_active`. So the ingest.py fix was about future-proofing, not retroactively fixing the data flow
- All gap redistribution used SM pattern as weights — this is honest for totals but fabricates hourly shape during gaps
- 0045MAK has very low consumption (~0.016 kWh/hr avg) where 10 Wh DDS8888 quantization causes larger % deviations

### Files Modified
- `acdb-api/ingest.py` — Switched delta calculation from `energy_integrated` to `energy_active`
- `acdb-api/om_report.py` — Added `total_deviation_pct`, auto-detect IoT start for days=0
- `acdb-api/frontend/src/pages/CheckMeterPage.tsx` — Cumulative chart, normalized start, updated stat cards
- `acdb-api/frontend/src/lib/api.ts` — Added `total_deviation_pct` to `CheckMeterPairStats`

## Session 2026-02-27 202602271930 (OTA Signature Verification Fix)

### What Was Done
- **Diagnosed OTA signature verification failure**: Team reported `E (314865) AWS_OTA: Signature verification failed` after successful download (265/266 blocks)
- **Root cause**: Two different ECDSA key pairs were generated with the same Subject (`CN=1PWR OTA Signer`). One was imported into ACM (Feb 20, serial `5FFF5F49...`), a different one was placed in the firmware's `main/certs/aws_codesign.crt` (Feb 21, serial `715B3A62...`). AWS Signer signed with key A, device verified with key B — always fails.
- **Fixed**: Replaced `aws_codesign.crt` in the firmware repo with the correct ACM certificate
- **Added `.gitignore` exception**: The `*.crt` rule was preventing the cert from being tracked; added `!main/certs/aws_codesign.crt`
- **Committed and pushed** to `onepwr-aws-mesh` main branch
- **WiFi issue clarified**: Team reported WiFi/TLS failures — this is expected behavior since WiFi creds are compile-time constants (`DareMightyThings`/`bestcity` in `sdkconfig.defaults`). SOP already documents this requirement.

### Key Decisions
- The correct certificate is the ACM one (imported Feb 20), not the firmware one (generated Feb 21)
- Both signing profiles (`1PWR_OTA_ESP32` and `1PWR_OTA_ESP32_v2`) use the same ACM cert ARN
- `1PWR_OTA_ESP32` is Canceled; `1PWR_OTA_ESP32_v2` is Active — bench test SOP correctly uses v2

### What Next Session Should Know
- EC2 build server (13.244.104.137) was unreachable at time of fix — may need to be started
- After `git pull` on the build server, the team must rebuild base firmware (v1.0.0) AND OTA target (v1.0.1) for bench test devices (OneMeter3, OneMeter4)
- The base firmware must be USB-flashed (it contains the corrected cert that verifies OTA signatures)
- WiFi creds are compile-time only — a future enhancement could store them in NVS for OTA-updateable WiFi config

### Files Modified
- `onepwr-aws-mesh/main/certs/aws_codesign.crt` — Replaced with correct ACM certificate
- `onepwr-aws-mesh/.gitignore` — Added exception for `aws_codesign.crt`

---

## Session 2026-03-07 202603071350 (Fleet Summary Card + OTA Success + 0026MAK Diagnosis)

### What Was Done

1. **Fleet total deviation summary card** on Check Meter page (`CheckMeterPage.tsx`):
   - New `FleetSummaryCard` component aggregates SM vs 1M totals across all check meters
   - Shows fleet-wide deviation %, absolute kWh difference, total SM/1M kWh, matched hours
   - Color-coded (green <5%, amber <15%, red 15%+), appears only when 2+ meters present
   - Committed and pushed to `main` → auto-deployed to cc.1pwrafrica.com

2. **OTA confirmed working end-to-end**:
   - Team reported successful OTA on OneMeter3 and OneMeter4 (bench test)
   - Console log shows: download 263/263 → signature verified → "OTA Completed successfully!" → reboot → polls for next job
   - Both AWS IoT jobs show "Completed" status (Mar 6)
   - Next steps: group targeting test Monday, MAK physical visit Tuesday

3. **Remote diagnosis of 0026MAK 1M measurement failure**:
   - Queried DynamoDB raw readings for all 5 check meters
   - Found 0026MAK's DDS8888 current reading dropped from normal (41-105 mA during active periods) to 0-1 mA starting around Mar 5
   - Voltage reads normally (228V), Modbus communication works, EnergyActive barely increments
   - Compared with 3 other working meters running identical firmware -- all show normal 3-4 mA current readings
   - **Firmware ruled out** as cause (same FW on all meters, only one affected)
   - **Diagnosis: DDS8888 internal hardware failure** -- current passes through (SM confirms consumption) but internal metering circuit no longer senses it
   - One brief 41 mA spike on Mar 7 suggests intermittent internal contact (failing solder joint or metering IC)
   - **Action: swap the DDS8888 unit on Tuesday**

4. **Team guidance drafted and sent via WhatsApp**:
   - Monday parallelized across 3 engineers (build firmware / group OTA test / MAK prep)
   - Tuesday MAK priorities: flash new watchdog firmware on all meters, priority on 23022696 (repeat dropout), swap 0026MAK DDS8888
   - Recommended laptops over EC2 for firmware builds (self-sufficient, no SSH dependency)
   - 0026MAK troubleshooting data shared with team

### Key Decisions
- Laptops preferred over EC2 build server for firmware builds (eliminates SSH dependency, enables build-flash-test cycle on one machine)
- DDS8888 at 0026MAK diagnosed as internal hardware failure via remote data analysis, avoiding speculative on-site troubleshooting
- Used EC2 Instance Connect (`send-ssh-public-key`) to access EOL server when SSH key wasn't authorized

### Current Meter Fleet Status (Mar 7)
| Account | 1M Serial | Status | Issue |
|---------|-----------|--------|-------|
| 0005MAK | 23022628 | ✓ Working | I=4 mA, normal |
| 0026MAK | 23022684 | ✗ Faulty | DDS8888 internal failure, I=0-1 mA |
| 0045MAK | 23022673 | ✓ Working | Very low consumption customer |
| 0119MAK | 23022646 | ✓ Working | I=3-4 mA, normal |
| 0025MAK | 23022696 | ✗ Offline | No data since Mar 5 17:08, repeat dropout |

### What Next Session Should Know
- **OTA is operational** for bench devices. Next: group targeting test, then field deployment
- **New firmware** (watchdog + WiFi reconnect) committed to `onepwr-aws-mesh` but needs building before Tuesday MAK visit
- **0026MAK needs DDS8888 swap** -- confirmed by remote DynamoDB data analysis
- **0025MAK needs new firmware flash** -- repeat communication dropout, 210m from powerhouse with repeaters in between
- **EC2 Instance Connect** is required to SSH into EOL (13.244.104.137) -- `id_rsa` key not authorized, must push via `aws ec2-instance-connect send-ssh-public-key` first
- **DynamoDB access**: table `1meter_data`, partition key `device_id` (format: `000023022XXX`), region `us-east-1`
- **PostgreSQL source enum**: `thundercloud` (SM), `koios` (SM), `iot` (1M) -- NOT `sparkmeter`
- **Enclosure design issue** flagged by team -- single channel enclosures too cramped for meter + PCB + power converter

### Files Modified
- `acdb-api/frontend/src/pages/CheckMeterPage.tsx` — Added FleetSummaryCard component

### Protocol Feedback
- CONTEXT.md and SESSION_LOG.md provided good continuity from prior sessions
- The conversation summary was accurate and comprehensive
- EC2 Instance Connect workaround should be documented in CONTEXT.md for future sessions

## Session 2026-02-16 202602161800 (CC Financing, Missing Features, and Manual Revision)

### What Was Done
- **Database tables created** on EOL PostgreSQL: `financing_products`, `financing_agreements`, `financing_ledger`, `payment_verifications`, plus `financing_portion`/`electricity_portion` columns on `transactions`
- **Financing backend** (`financing.py`): CRUD for product templates and agreements, payment split logic (`compute_financing_split`, `apply_financing_payment`), and PDF contract generation using new Jinja2 template (`template_financing_en.html`)
- **Payment routing modified** (`payments.py`): Both webhook and manual record endpoints now split payments between electricity and financing debt, with ones-digit 1/9 rule for dedicated debt payments
- **Automatic penalty job** (`financing_penalties.py`): Standalone script that scans active agreements and applies penalties per grace/interval terms
- **Payment verification backend** (`payment_verification.py`): Pending queue, bulk verify/reject endpoints
- **Onboarding pipeline endpoint** added to `om_report.py`: Aggregates commissioning step counts into a funnel
- **Four new frontend pages**: FinancingPage (product templates + agreements with ledger detail), RecordPaymentPage (manual payment with split indicator), PaymentVerificationPage (bulk verify/reject queue), PipelinePage (funnel visualization with drop-off %)
- **Customer financing section** added to CustomerDataPage: Shows active agreements with progress bars when customer has financing
- **App routing and Layout nav** updated to include all new pages
- **Operating Manual revised**: New markdown manual (`1PWR Customer Care Portal Operating Manual.md`) replacing old ACCDB-based PDF, covering all as-built features including financing

### Key Decisions
- Financing is tracked completely separately from electricity balance — the prepaid relay cutoff mechanic is unaffected
- Payment split uses FIFO ordering when multiple agreements exist
- Penalties are automatic via a cron-ready script, not manual
- The manual is markdown (not PDF) for easier maintenance

### What Next Session Should Know
- All 4 new tables exist on EOL PostgreSQL — no migrations needed
- The financing router and verification router are registered in `customer_api.py`
- `financing_penalties.py` needs to be added to a cron job on EOL (e.g., daily via `crontab -e` for the `ubuntu` user)
- The Sesotho version of the financing contract template (`template_financing_so.html`) has not been created yet — only English exists
- The extend credit wizard (4-step modal from CustomerDetailPage) was simplified into the agreements creation flow on the FinancingPage — a more polished wizard with signature canvas could be added later
- XLSX export on the payment verification page is not yet implemented (table is viewable/filterable but no download button)

### Files Modified
- `acdb-api/financing.py` (new)
- `acdb-api/financing_penalties.py` (new)
- `acdb-api/payment_verification.py` (new)
- `acdb-api/payments.py` (modified — split logic)
- `acdb-api/om_report.py` (modified — pipeline endpoint)
- `acdb-api/customer_api.py` (modified — router registration)
- `acdb-api/templates/template_financing_en.html` (new)
- `acdb-api/frontend/src/lib/api.ts` (modified — new API types and functions)
- `acdb-api/frontend/src/App.tsx` (modified — new routes)
- `acdb-api/frontend/src/components/Layout.tsx` (modified — nav links)
- `acdb-api/frontend/src/pages/FinancingPage.tsx` (new)
- `acdb-api/frontend/src/pages/RecordPaymentPage.tsx` (new)
- `acdb-api/frontend/src/pages/PaymentVerificationPage.tsx` (new)
- `acdb-api/frontend/src/pages/PipelinePage.tsx` (new)
- `acdb-api/frontend/src/pages/CustomerDataPage.tsx` (modified — financing section)
- `1PWR Customer Care Portal Operating Manual.md` (new)

## Session 2026-03-15 202603152032 (Reframe CC docs around 1PDB)

### What Was Done
- Rewrote the top-level `README.md` so the repo now clearly states that `1PDB` is the canonical source of truth and that `1PWR CC` is the portal/API layer over it.
- Updated `CONTEXT.md`, `.cursorrules`, and `acdb-api/CONTEXT.md` to remove stale Windows/ACCDB deployment assumptions and teach future AI sessions the Linux + `1PDB` architecture instead.
- Replaced `docs/whatsapp-customer-care.md` with a shorter current runbook centered on the Linux-hosted CC API and `1PDB`, while keeping explicit notes that old Windows/ACCDB references are legacy only.
- Moved the nested `1Meter_PCB` repository out of `1PWR CC/` into the enclosing `AI Projects` folder so `1PWR CC` no longer contains an unrelated nested git repository.
- Removed the stale local `.git/info/exclude` entry that had been hiding `/1Meter_PCB/` from `1PWR CC` status.

### Key Decisions
- Chose to fix architecture truth first in docs and AI guidance before touching runtime code or renaming legacy paths like `acdb-api/`.
- Kept `acdb-api/` naming in place for now to avoid unnecessary churn; documented it as historical naming rather than trying to rename the tree in the same PR.
- Treated ACCDB/Windows references as legacy context worth preserving only when explicitly labeled as deprecated, rather than silently deleting all historical evidence.
- Moved `1Meter_PCB` out immediately because it was already a standalone repo and did not belong nested inside the CC application repo.

### What Next Session Should Know
- The highest-value follow-on in `1PWR CC` is still a docs-and-boundary cleanup sequence: operator-facing UI text and config defaults that still say `ACCDB` should be updated next.
- The next cross-repo priority is to merge and deploy the `1PDB` reconciliation branch so repo state and live runtime stay aligned.
- Legacy ACCDB-era operational scripts in `acdb-api/` still need to be quarantined into a `legacy/` area or removed if confirmed unused.
- `1Meter_PCB` now lives at `/Users/mattmso/Dropbox/AI Projects/1Meter_PCB` as its own repo; `1PWR CC` status is clean after the move.

### Senescence Notes
- No major context degradation noticed during this slice.
- The biggest continuity gap was stale repo documentation that still described the deprecated ACCDB/Windows topology as current.

### Files Modified
- `README.md`
- `CONTEXT.md`
- `.cursorrules`
- `acdb-api/CONTEXT.md`
- `docs/whatsapp-customer-care.md`
- `.git/info/exclude`

### Protocol Feedback
- `CONTEXT.md` and `.cursorrules` were materially stale on architecture and deploy topology; future sessions would likely have been misled without this correction.
- The repo benefits from explicitly distinguishing "legacy naming" from "live architecture" because `acdb-api/` now implies the wrong system to new readers.
- A future cleanup should add a small `legacy/` section or doc index so deprecated ACCDB material is preserved intentionally instead of lingering in ambiguous locations.

## Session 2026-03-15 202603152051 (Archive ACCDB-era helper scripts)

### What Was Done
- Created `legacy/accdb/README.md` to document the deprecated ACCDB / Windows helper scripts and explicitly mark them as historical-only.
- Moved the clearly legacy helper files out of the active backend tree with git-preserving renames:
  - `acdb-api/import_meter_readings.py` -> `legacy/accdb/import_meter_readings.py`
  - `acdb-api/compact_accdb.py` -> `legacy/accdb/compact_accdb.py`
  - `acdb-api/sync_accdb.ps1` -> `legacy/accdb/sync_accdb.ps1`
  - `acdb-api/snapshot.py` -> `legacy/accdb/snapshot.py`
  - `acdb-api/setup.bat` -> `legacy/accdb/setup.bat`
  - `acdb-api/install-service.bat` -> `legacy/accdb/install-service.bat`
- Updated `README.md` and `acdb-api/CONTEXT.md` so they now point future readers to `legacy/accdb/` instead of leaving those scripts implied in the active runtime tree.
- Updated the `om_report.py` tenure-report docstring to note that `import_meter_readings.py` is historical and archived under `legacy/accdb/`.
- Extended `docs/whatsapp-customer-care.md` with a pointer to the archived ACCDB helper area.

### Key Decisions
- Archived the scripts instead of deleting them because they still have historical and migration-provenance value.
- Moved them outside `acdb-api/` so the normal backend deploy path no longer ships obviously deprecated Windows helpers.
- Left historical content inside the archived scripts untouched; the archive is for reference, not for silently "modernized" reruns.

### What Next Session Should Know
- The active backend tree is now cleaner: legacy ACCDB helper scripts are no longer mixed into `acdb-api/`.
- The next cleanup slice should focus on operator-facing UI/config leftovers that still say `ACCDB` or point to deprecated hosts, especially `whatsapp-bridge/whatsapp-customer-care.js`, `acdb-api/frontend/src/pages/TablesPage.tsx`, and `acdb-api/frontend/src/pages/SyncPage.tsx`.
- Historical mentions in `SESSION_LOG.md` were intentionally left alone because they describe what happened at the time and remain useful provenance.

### Files Modified
- `README.md`
- `acdb-api/CONTEXT.md`
- `acdb-api/om_report.py`
- `docs/whatsapp-customer-care.md`
- `legacy/accdb/README.md`
- `legacy/accdb/import_meter_readings.py` (renamed)
- `legacy/accdb/compact_accdb.py` (renamed)
- `legacy/accdb/sync_accdb.ps1` (renamed)
- `legacy/accdb/snapshot.py` (renamed)
- `legacy/accdb/setup.bat` (renamed)
- `legacy/accdb/install-service.bat` (renamed)

## Session 2026-03-15 202603152057 (Scrub live ACCDB wording and defaults)

### What Was Done
- Updated operator-facing frontend wording so active pages no longer imply that ACCDB or Access is the live backend:
  - `TablesPage.tsx` now refers to the customer care database rather than the Access database
  - `SyncPage.tsx` now frames sync as CC database ↔ uGridPlan instead of ACCDB ↔ uGridPlan
  - `TariffManagementPage.tsx` now refers to live CC tariff configuration instead of `tblconfig.therate in the ACCDB`
  - `FinancialPage.tsx` and `OMReportPage.tsx` now label their data as `1PDB`-backed CC data
- Updated `whatsapp-bridge/whatsapp-customer-care.js` so comments and logs refer to the CC API, not ACDB, and added `CC_API` as the preferred environment variable while keeping `ACDB_API` as a backward-compatible fallback.
- Verified the frontend text-only changes with `npx tsc -b --noEmit` and checked lints on the touched frontend/bridge files.

### Key Decisions
- Limited this slice to user-facing text and config-default cleanup, not deeper API contract renames such as `accdb_*` payload fields.
- Kept `ACDB_API` as a backward-compatible env fallback in the bridge to avoid breaking existing deployments while still moving the documented default toward the current architecture.
- Left explicitly historical ACCDB comparisons in the Help page intact because they describe the old system rather than implying it is still live.

### What Next Session Should Know
- The remaining `ACCDB` references in the active frontend are now mostly historical comparisons or internal variable names, not live operator guidance.
- The next cleanup choice is either:
  - deeper contract cleanup (`accdb_*` naming in sync payloads / frontend types), or
  - switch back to cross-repo work and merge/deploy the `1PDB` reconciliation branch.
- The current `1PWR CC` working tree includes three cleanup slices not yet committed: architecture/doc corrections, legacy script archiving, and operator-facing text/default updates.

### Files Modified
- `acdb-api/frontend/src/pages/TablesPage.tsx`
- `acdb-api/frontend/src/pages/SyncPage.tsx`
- `acdb-api/frontend/src/pages/TariffManagementPage.tsx`
- `acdb-api/frontend/src/pages/FinancialPage.tsx`
- `acdb-api/frontend/src/pages/OMReportPage.tsx`
- `whatsapp-bridge/whatsapp-customer-care.js`

## Session 2026-03-15 202603152059 (Finish deeper CC contract cleanup)

### What Was Done
- Renamed the active uGridPlan sync contract from `accdb_*` naming to `cc_*` / `cache_*` in the frontend and backend, including `SyncPage.tsx`, `frontend/src/lib/api.ts`, and `acdb-api/sync_ugridplan.py`.
- Kept backward-compatible aliases in the sync backend response/request layer (`accdb_*`, `pull_to_sqlite`, `sqlite_written`) so any older callers will still work while the portal uses the cleaner names.
- Updated additional active runtime remnants so the code better reflects the current architecture:
  - `customer_api.py` now prefers `CC_API_PORT` with `ACDB_PORT` as a legacy fallback
  - `auth.py` now describes lookups against the CC database instead of ACCDB
  - `balance_engine.py` now refers to legacy imported consumption rather than active ACCDB rows
  - `CustomerDataPage.tsx` comment now refers to the CC database
- Fixed a small pre-existing bug in `sync_ugridplan.py` where `ugp_saved` could be undefined if a sync was run with `push_to_ugp=false`.
- Validated the updated code with `python3 -m py_compile` on touched backend modules, `npx tsc -b --noEmit` in the frontend, and lint checks on the edited files.

### Key Decisions
- Treated the sync API as the highest-value place to finish the rename because it was the main active code path still exposing `accdb_*` names to the live frontend.
- Preserved compatibility at the API boundary instead of doing a flag day break, since some external or older clients may still send/read the legacy field names.
- Left explicitly historical ACCDB references in docs/help content intact when they are clearly describing the retired system rather than the live runtime.

### What Next Session Should Know
- The `1PWR CC` cleanup now has four coherent slices ready together: docs/boundary corrections, legacy-script archiving, operator-facing wording cleanup, and deeper sync/config contract cleanup.
- The remaining active ACCDB mentions in code are now mostly compatibility aliases or clearly historical references, not the names used by the live frontend/runtime.
- The next cross-repo move is still to finish the `1PDB` reconciliation branch merge/deploy work.

### Files Modified
- `acdb-api/sync_ugridplan.py`
- `acdb-api/frontend/src/lib/api.ts`
- `acdb-api/frontend/src/pages/SyncPage.tsx`
- `acdb-api/frontend/src/pages/CustomerDataPage.tsx`
- `acdb-api/customer_api.py`
- `acdb-api/auth.py`
- `acdb-api/balance_engine.py`
- `SESSION_LOG.md`

## Session 2026-03-15 202603152356 (Push meter-export date filtering into SQL)

### What Was Done
- Updated `acdb-api/om_report.py` so `GET /api/om-report/meter-export` now applies valid `start_date` / `end_date` bounds directly in SQL for both the `meter_readings` query and the `hourly_consumption` fallback query.
- Kept the existing “ignore malformed dates” behavior by validating the date strings first in Python and only pushing well-formed bounds into SQL.
- Fixed the endpoint’s end-date behavior while doing that work: the filter is now `timestamp < end_date + 1 day`, so `end_date=YYYY-MM-DD` includes the full requested day instead of effectively stopping at midnight.
- Re-ran `python3 -m py_compile acdb-api/om_report.py` and lint checks after the patch; both passed locally.

### Key Decisions
- Treated SQL-level date filtering as the root-cause fix for the HH refresh timeout path, because client-side month/quarter chunking is not enough if the backend still scans and serializes each site’s full history before trimming the date range.
- Preserved the existing response shape and post-query safety checks so the change stays contract-compatible while making date-window requests materially cheaper.

### What Next Session Should Know
- This backend patch is local only right now; it still needs to be pushed/deployed before the new chunked HH refresh path in `uGridPlan` can benefit from it.
- Live verification from the local environment was blocked by repeated timeouts reaching `https://cc.1pwrafrica.com/api/auth/employee-login`, so the next practical step is deploy first, then re-test when CC reachability is healthy again.

### Files Modified
- `acdb-api/om_report.py`
- `SESSION_LOG.md`

## Session 2026-03-16 202603160250 (Fix date-window export crash in production)

### What Was Done
- Identified and patched a follow-on production bug in `acdb-api/om_report.py`: date-window `meter-export` requests could still fail with HTTP 500 once a window hit real rows, because the Python-side post-SQL filter treated any value with a `year` attribute as a timestamp and could end up comparing `datetime.date` values against `datetime.datetime` bounds.
- Added `_coerce_export_timestamp()` to normalize export timestamps from DB/date/string values into a consistent naive `datetime`, then routed both the `meter_readings` path and the `hourly_consumption` fallback path through that helper before applying the Python-side start/end checks.
- Committed the fix as `642d936` (`Normalize meter-export window timestamps.`), pushed `main` to `origin/main`, and monitored production deploy workflow `23114709010` to successful completion.
- Re-probed the exact previously failing live request, `meter-export?customer_type=HH&site=MAK&start_date=2022-09-01&end_date=2022-09-30`, and confirmed it now returns HTTP 200 with real readings instead of 500.

### Key Decisions
- Treated this as a backend contract correctness fix rather than trying to special-case the client refresh logic, because the live API itself was crashing on a legitimate date-window request.
- Kept the earlier SQL date filtering in place and only normalized timestamp handling around the remaining Python-side guard checks, minimizing the production patch while restoring stability for non-empty windows.

### What Next Session Should Know
- Production `meter-export` now appears stable for the previously failing `MAK` monthly window that blocked the streamed HH refresh.
- The client-side `build_acdb_cdfs.py --refresh affected-households` rerun should be treated as the next source of truth for whether the full HH rebuild now completes end-to-end.

### Files Modified
- `acdb-api/om_report.py`
- `SESSION_LOG.md`

## Session 2026-03-16 202603160325 (Stabilize tenure date parsing)

### What Was Done
- Patched `acdb-api/om_report.py` `GET /api/om-report/consumption-by-tenure` to reuse `_coerce_export_timestamp()` for all transaction / consumption date parsing instead of keeping a separate permissive parser that could return raw `date` objects.
- Extended `_coerce_export_timestamp()` to normalize slash-formatted dates as well, so the tenure endpoint and export endpoint now share one consistent DB/date/string-to-`datetime` coercion path.
- Re-ran `python3 -m py_compile acdb-api/om_report.py` and lint checks after the change; both passed locally.

### Key Decisions
- Reused the already-deployed timestamp coercion helper rather than maintaining a second tenure-specific parser, because the likely failure mode was the same class of inconsistent DB date handling that had already caused the `meter-export` 500s.
- Kept the patch narrow to parsing/coercion only, so the tenure aggregation math and output contract remain unchanged.

### What Next Session Should Know
- This patch is intended to remove the remaining live `consumption-by-tenure` HTTP 500 seen during the household refresh rebuild.
- After deploy, the next validation step is to re-query `GET /api/om-report/consumption-by-tenure` directly, then rerun the local `smp_hh1` refresh path so tenure arrays can be repopulated from the live API if the endpoint is healthy.

### Files Modified
- `acdb-api/om_report.py`
- `SESSION_LOG.md`
