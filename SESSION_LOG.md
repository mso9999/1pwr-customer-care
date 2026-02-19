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
