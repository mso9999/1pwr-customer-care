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
