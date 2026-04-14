# 1PWR Customer Care Context (AI Orientation)

> **Purpose**: This file provides essential context for AI assistants working on the CC portal.
> Read this at the start of every conversation. For detailed documentation, see `README.md`
> and `docs/whatsapp-customer-care.md`.

## What This Project Is

**1PWR Customer Care** is a web portal and operations system for managing minigrid customers in Lesotho. It provides:
- Customer database management (search, view, edit)
- O&M quarterly reporting with analytics charts
- Financial analytics (ARPU time series, revenue by site)
- Tariff management
- Billing and payment tracking
- WhatsApp-based customer care ticketing (automated via AI)

**Domain**: Rural minigrids (solar+battery), starting in Lesotho, expanding to Benin and Zambia.
**Operated by**: 1PWR Africa / OnePower Lesotho.
**Users**: 1PWR operations staff, finance team, customer care agents.
**Data Source**: 1PDB (PostgreSQL 16) — canonical source of truth for customer, meter, billing, and ingestion data.

**Legacy naming note**: The repo still uses historical names like `acdb-api/`, but
that no longer implies Access / ODBC / Windows as the active runtime.

## Architecture (Post-Migration)

```
cc.1pwrafrica.com
       │
       ▼
┌──────────────────────────────────────────────────┐
│  Linux CC host - Caddy + FastAPI                │
│                                                    │
│  Static files → /opt/cc-portal/frontend/           │
│  /api/*       → reverse_proxy localhost:8100       │
│  /health      → reverse_proxy localhost:8100       │
│  /customers/* → reverse_proxy localhost:8100       │
│  /sites       → reverse_proxy localhost:8100       │
│                                                    │
│  FastAPI backend (this repo)                       │
│  1PDB PostgreSQL (master of record)                │
│  systemd: 1pdb-api, 1pdb-api-bn                    │
└──────────────────────────────────────────────────┘
       │
       ▼
┌──────────────┐
│ WhatsApp     │
│ Bridge       │
│ (Node.js)    │
│ PM2-managed  │
│ Creates O&M  │
│ tickets via  │
│ uGridPlan API│
└──────────────┘
```

The active production stack is Linux-hosted and `1PDB`-backed. Do not treat
Windows EC2 / ACCDB assumptions as current production architecture.

### Related Repos
- **onepowerLS/1PDB** — Database schema, migration scripts, data pipeline services
- **onepowerLS/SMSComms** — Lesotho SMS Gateway (sms.1pwrafrica.com)
- **onepowerLS/SMSComms-BN** — Benin SMS Gateway (smsbn.1pwrafrica.com)
- **onepowerLS/SMS-Gateway-APP** — Android Medic Gateway app (phones → PHP gateways)
- **onepowerLS/ingestion_gate** — Prototype meter IoT Lambda (DynamoDB)
- **onepowerLS/onepwr-aws-mesh** — 1Meter ESP32-C3 firmware (AWS IoT + Mesh-Lite + OTA)

### SMS payment gateways — where they are defined (two production stacks)

There are **two live gateway deployments** — **Lesotho** (M-Pesa) and **Benin** (MTN MoMo) — each with its **own** hosted PHP endpoint and DB-backed routing. They are **not** implemented inside this CC repo; CC only exposes **`POST /api/sms/incoming`** (LS) and **`POST /api/bn/sms/incoming`** (BN).

| Piece | Repo | Role |
|-------|------|------|
| **Server gateway (LS)** | [SMSComms](https://github.com/onepowerLS/SMSComms) | PHP on **sms.1pwrafrica.com** — `receive.php` ingests JSON from the Android app, writes typed files / DB, then **`mirror_to_1pdb()`** POSTs the same JSON to `https://cc.1pwrafrica.com/api/sms/incoming`. |
| **Server gateway (BN)** | [SMSComms-BN](https://github.com/onepowerLS/SMSComms-BN) (or **`smsbn/`** beside Lesotho in some checkouts of SMSComms) | PHP on **smsbn.1pwrafrica.com** — same pattern; mirror URL is **`/api/bn/sms/incoming`**. |
| **Phone app** | [SMS-Gateway-APP](https://github.com/onepowerLS/SMS-Gateway-APP) | Android (Medic fork): forwards SMS to the configured **webapp URL** (`receive.php`). One build; **each country** points the app at its own gateway host. |

**CC ingest** (`acdb-api/ingest.py`) is the **consumer** of those mirrors: parse payment text → 1PDB row → optional `credit_sparkmeter` to Koios/ThunderCloud. Any **second** path that credits SparkMeter for the same SMS (legacy PHP, Iometer, native Koios payment gateway, etc.) causes **double credits** — see deployment notes in `ingest.py` (`SMS_INGEST_PUSH_SPARKMETER`).

## Key Files

### Backend (`acdb-api/`)

| File | Purpose |
|------|---------|
| `customer_api.py` | Main FastAPI app -- mounts all routers, psycopg2 pool, CORS, auth |
| `om_report.py` | O&M analytics endpoints: customer stats, consumption, sales, ARPU |
| `crud.py` | Customer CRUD operations (PostgreSQL information_schema introspection) |
| `auth.py` | Authentication (employee login, JWT tokens) |
| `db_auth.py` | SQLite auth store (employee roles, customer passwords, legacy mutation source) |
| `middleware.py` | Auth middleware, role-based access |
| `models.py` | Pydantic models |
| `tariff.py` | Tariff management endpoints (system_config table) |
| `mutations.py` | PostgreSQL mutation audit log with legacy SQLite backfill |
| `exports.py` | Data export endpoints |
| `stats.py` | Dashboard statistics |
| `commission.py` | Commission workflow + bulk status update |
| `registration.py` | Customer registration + Excel bulk import |
| `contract_gen.py` | Contract PDF generation |
| `sparkmeter_customer.py` | CC → SparkMeter customer sync (auto-push on registration) |
| `sync_ugridplan.py` | Sync data to uGridPlan |
| `schema.py` | PostgreSQL schema introspection endpoints |
| `requirements.txt` | Python dependencies (psycopg2-binary, no pyodbc) |

### Frontend (`acdb-api/frontend/`)

| File | Purpose |
|------|---------|
| `src/App.tsx` | Main app, routing, layout |
| `src/lib/api.ts` | API client (all backend calls, types) |
| `src/pages/FinancialPage.tsx` | Financial analytics (ARPU charts, revenue tables) |
| `src/pages/OMReportPage.tsx` | O&M quarterly report |
| `src/pages/DashboardPage.tsx` | Main dashboard |
| `src/pages/CustomerListPage.tsx` | Customer search and list |
| `src/pages/TariffPage.tsx` | Tariff management |

### Infrastructure

| File | Purpose |
|------|---------|
| `.github/workflows/deploy.yml` | CI/CD: build frontend, deploy both components |
| `scripts/ops/cc_postgres_backup.sh` | Production logical backup job (Postgres + `cc_auth.db` to S3) |
| `scripts/ops/cc_postgres_restore_verify.sh` | Restore drill script for a disposable restore host |
| `docs/ops/postgres-backup-recovery.md` | Backup / restore runbook and production ownership boundary |
| `whatsapp-bridge/whatsapp-customer-care.js` | WhatsApp CC bridge |
| `docs/whatsapp-customer-care.md` | Full system documentation |

## Deployment

### Auto-Deploy (Primary Method)
Push to `main` triggers GitHub Actions with two parallel jobs:
- **deploy-frontend**: GitHub-hosted runner → `npm ci && npm run build` → `rsync` to Linux EC2
- **deploy-backend**: GitHub-hosted runner → `rsync` backend files to Linux EC2 → **`acdb-api/migrations/apply_migrations.sh`** against `onepower_cc` / `onepower_bj` (from `/opt/1pdb/.env` and `/opt/1pdb-bn/.env`) → restart `1pdb-api` services

This keeps **1PDB schema** in step with API code (avoids commissioning and other failures when new columns ship in migrations).

### Manual Access

| Target | Command |
|--------|---------|
| CC Linux host | `ssh -i "/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem" ubuntu@<current-cc-linux-host>` |
| uGridPlan EC2 | `ssh -p 2222 -i uGridPLAN.pem ugridplan@15.240.40.213` |

**SSH keys (human Mac):** Canonical Dropbox folder for team keys (CC, etc.): **`/Users/mattmso/Dropbox/AI Projects/secrets`** — use **`EOver.pem`** there for the CC host. Cloud/CI agents do not have this path; use GitHub secret **`EC2_SSH_KEY`** or copy into repo **`.secrets/`** (gitignored).

**Resolve `<current-cc-linux-host>` with AWS CLI** (preferred over stale IPs in docs):

```bash
# Replace filters with your org’s tags / instance id (region often af-south-1 for CC)
aws ec2 describe-instances --region af-south-1 \
  --instance-ids i-xxxxxxxxxxxxxxxxx \
  --query 'Reservations[0].Instances[0].PublicDnsName' --output text

# Or search by tag (example: Name contains “cc” — adjust to your naming)
aws ec2 describe-instances --region af-south-1 \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId,Tags[?Key==`Name`].Value|[0],PublicDnsName,PublicIpAddress]' \
  --output table
```

You can also use the **`EC2_LINUX_HOST`** value from GitHub Actions secrets (same hostname the deploy job uses). Avoid relying on historical public IPs in old docs.

### 1Meter Firmware Build Host

- Current remote firmware build host is the repurposed staging EC2 at `13.247.190.132` on port `2222`, accessed as `ubuntu`.
- Firmware workspace lives under `/opt/1meter-firmware`.
- Key paths on that host:
  - repo clone: `/opt/1meter-firmware/onepwr-aws-mesh`
  - ESP-IDF: `/opt/1meter-firmware/esp-idf`
  - environment bootstrap: `/opt/1meter-firmware/env.sh`
  - release bundles: `/opt/1meter-firmware/releases`
- Historical helper assets from the March 2026 1Meter workflow are archived in:
  - `docs/archive/2026-03-worktree-cleanup/1meter/`
  - `scripts/archive/2026-03-worktree-cleanup/1meter/`
- Current state: remote build is proven working, but S3 release publishing / OTA job automation is still a next step.

### Service Management

| Service | How to manage |
|---------|---------------|
| Caddy (frontend) | `ssh ubuntu@<current-cc-linux-host> "sudo systemctl reload caddy"` |
| CC Bridge | `ssh ubuntu@<current-cc-linux-host> "pm2 restart whatsapp-cc"` |
| CC API (LS) | `ssh ubuntu@<current-cc-linux-host> "sudo systemctl restart 1pdb-api"` |
| CC API (BN) | `ssh ubuntu@<current-cc-linux-host> "sudo systemctl restart 1pdb-api-bn"` |

### Backup and Restore (2026-04-01)

- Production CC host: `EOL` (`i-04291e12e64de36d7`, `af-south-1`). It is under the regional DLM/EBS snapshot policy via the `backup=yes` tag, but snapshots are only host-level DR.
- Logical backups now run on the production host via:
  - systemd timer: `cc-postgres-backup.timer`
  - env file: `/etc/default/cc-postgres-backup`
  - script: `/usr/local/bin/cc_postgres_backup.sh`
- Each run writes local artifacts under `/var/backups/1pwr-cc/<UTC timestamp>/` and uploads them to:
  - `s3://1pwr-cc-backups-758201218523-af-south-1/customer-care/ip-172-31-3-91/<UTC timestamp>/`
- Artifacts per run:
  - `onepower_cc.dump`
  - `onepower_bj.dump`
  - `cc_auth.db.backup`
  - `manifest.json`
  - `*.sha256`
- Restore drills must run on a disposable restore host or workstation with PostgreSQL tooling and at least ~120 GiB free disk. The production CC root volume was expanded to `120 GiB` on `2026-04-02`, but full restore drills should still stay off the live host to avoid PostgreSQL contention and recovery risk during production operations.
- Latest verified restore drill:
  - backup timestamp: `20260401T210624Z`
  - restore report uploaded beside the backup in S3 as `restore-verify-20260401211803.json`
  - verified counts:
    - LS restore: `customers=1465`, `accounts=1464`, `meters=1735`, `cc_mutations=1950`
    - BN restore: `customers=165`, `accounts=165`, `meters=162`, `cc_mutations=1950`
- Audit ledger cutover:
  - migration: `acdb-api/migrations/005_create_cc_mutations.sql`
  - helper: `scripts/ops/backfill_cc_mutations.py`
  - both country databases now have their own `cc_mutations` table; the shared legacy SQLite mutation history was backfilled into both during cutover.

## Site Codes (Lesotho Minigrids)

| Code | Name | Code | Name |
|------|------|------|------|
| MAK | Ha Makebe | SEH | Sehlabathebe |
| MAS | Mashai | TLH | Tloha-re-Bue |
| SHG | Sehonghong | RIB | Ribaneng |
| LEB | Lebakeng | KET | Ketane |
| MAT | Matsieng | RAL | Ralebese |
| SEB | Semonkong | SUA | Ha Suoane |
| TOS | Tosing | DON | Ha Nkone |

## Metering Architecture

### Meter Roles
The `meters` table has a `role` column (enum: `primary`, `check`, `backup`):
- **primary**: Billing/production meter. Data used in consumption aggregation and customer dashboard.
- **check**: Verification meter installed in series with primary during testing. Data stored but excluded from customer-facing aggregates.
- **backup**: Standby meter. Not currently active.

When promoting a check meter to primary (via `PATCH /api/meters/{id}/role`), the old primary
on that account is auto-demoted.

### Prototype 1Meters
Three 1Meter prototypes are installed at MAK in series with SparkMeters for validation:
- 23022628 → 0005MAK (check), SparkMeter 57408 (primary)
- 23022696 → 0025MAK (check), SparkMeter 58431 (primary)
- 23022673 → 0045MAK (check), SparkMeter 41657 (primary)
- 23022667 — repeater/gateway node at powerhouse (NOT a customer meter, not in meters table)
- 23022613 — real meter, customer TBD (pending team confirmation)

New meters are registered with one `INSERT INTO meters` row — the ingest API resolves
meters dynamically from the DB (no hardcoded dicts).

**Energy resolution**: The DDS8888 Modbus register reports energy in 0.01 kWh (10 Wh) steps.
Firmware implements power integration (trapezoidal rule on `activePowerW`) to get ~0.8 Wh
resolution, published as `EnergyIntegrated` alongside `EnergyActive` in the MQTT payload.
Backend uses `EnergyIntegrated` for delta calculations when available.

**Timestamps**: 1Meters report in SAST (UTC+2). `ingest.py` and `prototype_sync.py` convert
to UTC before storing. The CC portal's `_to_local()` converts back to SAST for display.

**PCB design note for next revision**: Route the DDS8888 CF (pulse output) pin to an ESP32
GPIO with PCNT support. This would enable hardware-accurate pulse counting at 1200 imp/kWh
(0.83 Wh resolution) without the drift inherent in power integration. Current PCB (v2.2)
connects to DDS8888 exclusively via RS485 Modbus.

**Repo boundary note**: `1Meter_PCB` is the KiCad hardware repo. The live ESP32 firmware source is
in `onepowerLS/onepwr-aws-mesh`. Do not expect MQTT / TLS / OTA code to live in the PCB repo.

### Data Sources
| Source | Platform | Coverage | Ingestion |
|--------|----------|----------|-----------|
| Koios v2 historical | SparkMeter Cloud | KET, LSB, MAS, MAT, SEH, SHG, TLH (LS) + GBO, SAM (BN). RIB/TOS not yet operational. | `import_hourly.py` (incremental commits, `--no-skip` for gap-filling) + systemd timer |
| ThunderCloud parquet | SparkMeter on-prem | MAK | `import_thundercloud.py` (batch, ~1 day lag, `ON CONFLICT DO UPDATE` for gap-fill) |
| ThunderCloud v0 live | SparkMeter on-prem | MAK | `import_tc_live.py` (cumulative register diffs, non-lossy, 15-min intervals) |
| ThunderCloud web API | SparkMeter on-prem | MAK | `import_tc_transactions.py` (live transactions) |
| IoT / ingestion_gate | 1Meter prototype | MAK (3 meters) | Real-time Lambda → POST /api/meters/reading |
| SMS Gateway (LS) | M-PESA payments | All LS sites | sms.1pwrafrica.com mirrors to `POST /api/sms/incoming` (`ingest.py`) — **Remark-first** account resolution (`mpesa_sms.resolve_sms_account`), **phone lookup fallback**; WhatsApp alert on phone fallback via `CC_BRIDGE_NOTIFY_URL` / `CC_BRIDGE_SECRET` (Benin: `CC_BRIDGE_*_BN` when `COUNTRY_CODE=BN`). Writes **1PDB** (incl. `sms_payer_phone`, `sms_remark_raw`, `sms_allocation`, `payment_reference`) and **pushes credit** via `credit_sparkmeter` unless `SMS_INGEST_PUSH_SPARKMETER=0`. Historical log reconciliation: `scripts/ops/reconcile_sms_misroutes_from_logs.py` (partial if SMS text in logs is truncated). |
| SMS Gateway (BN) | MTN MoMo payments | All BN sites | smsbn.1pwrafrica.com mirrors JSON to **`POST /api/bn/sms/incoming`** (same payload as LS). **Benin API** (`COUNTRY_CODE=BN`, port 8101): `momo_bj.parse_momo_bn_sms` + `resolve_bn_momo_account` (Motif/account text first, then phone **229** lookup). 1PDB + SparkMeter + WhatsApp fallback as in `ingest.py`. Koios consumption sync uses **`country_config`** sites/keys for BN. |

**Lesotho duplicate-credit guard (SMSComms repo):** `receive.php` mirrors JSON to CC **and** legacy PHP wrote payment files consumed by `sparkmeter/new_file_watcher.php`, which posted to **ThunderCloud** — a second SparkMeter credit alongside CC. `$LEGACY_FILE_WATCHER_CREDIT_ENABLED = false` in `sparkmeter/env.php` disables that payment-file crediting path; CC mirror remains the sole automatic creditor. **Deploy:** LS and BN are **two separate** cPanel targets; production is often **manual** (archive old PHP first). See `docs/ops/sms-gateway-cpanel-deploy.md`.

### BN (Benin) Data Pipeline

Benin runs two sites: **GBO** (Gbowélé) and **SAM** (Samondji), both using SparkMeter Nova meters via **Koios**.

**Data flow** (all run via `sync_consumption.sh` Phase 3, every 15 minutes):

| Component | Script | Method | Timer Arg |
|-----------|--------|--------|-----------|
| Hourly consumption | `import_hourly_bn.py` | Koios web UI daily report CSV download | `$WEEK_AGO` (7-day rolling window for gap recovery) |
| Transactions (payments) | `import_transactions_bn.py` | Koios web UI payment CSV download | `$YESTERDAY` |
| Customer types | `sync_bn_customer_types.py` | Koios web session + census spreadsheet | (no date arg) |

**Key differences from LS pipeline**:
- BN uses **web session scraping** (not Koios v1/v2 API) because the BN org is not API-enabled for reads
- The hourly script downloads daily reading CSVs, bins 15-min intervals into hourly buckets, inserts with `ON CONFLICT DO NOTHING`
- BN `accounts` table has no `status` column (unlike LS)

**Balance computation**: `balance_engine.get_balance_kwh()` works the same for BN:
`balance = SUM(payment kWh from transactions) - SUM(hourly consumption) - SUM(legacy debits)`

**Balance audit** (`audit_bn_balances.py`):
- Fetches Koios credit balances via web session (`GET /sm/organizations/{ORG_ID}/customers` with JSON accept)
- Converts XOF balances to kWh using tariff rate (160 XOF/kWh)
- Compares against 1PDB computed balances
- `--check` mode: daily monitoring via `1pdb-bn-audit.timer` (06:00 UTC), exits 1 on drift
- `--reconcile --apply`: inserts `balance_seed` transactions to zero out deltas

**Initial reconciliation**: Performed 2026-04-09. 152 valid accounts seeded, 12 garbage account codes skipped.

### SparkMeter API Landscape (as of 2026-02-19)

**Koios v1 (management)**: `https://www.sparkmeter.cloud/api/v1/`
- `POST /payments` — credit with `customer_code` directly (single call, no UUID lookup)
- `GET /payments?external_id=X` — idempotency lookup
- `POST /payments/{id}/reverse` — reverse a payment
- `GET /customers?code=X` — customer lookup by account number
- Auth: `X-API-KEY` + `X-API-SECRET` headers. Country-specific keys in `.env`

**Koios v2 (data)**: `https://www.sparkmeter.cloud/api/v2/`
- `POST /organizations/{org_id}/data/freshness` — per-site data availability dates
- `POST /organizations/{org_id}/data/historical` — hourly readings, S3-backed, ~1 day lag
- `POST /organizations/{org_id}/data/live` — **not functional for our sites** (times out; requires Nova meter type + service area config)
- Org ID: `1cddcb07-6647-40aa-aaaa-70d762922029` (LS), `0123589c-7f1f-4eb4-8888-d8f8aa706ea4` (BN)
- Filter format: `{"sites": [site_uuid], "date_range": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}}`
- Rate limits: **30,000 requests per day per org** (hard daily budget), plus burst limit ~3 req/5 sec
- Rate limit is per-org: LS and BN have independent quotas
- `import_hourly.py` handles 429 gracefully: stops immediately, skips remaining sites for that org

**ThunderCloud v0**: `https://sparkcloud-u740425.sparkmeter.cloud/api/v0/`
- `GET /customers?reading_details=true` — live meter readings + cumulative energy registers
  - Per-customer: `code`, `credit_balance`, `debt_balance`, `meters[]`
  - Per-meter: `serial`, `current_daily_energy`, `total_cycle_energy`, `last_energy`, `last_energy_datetime`, `latest_reading{}`
  - Per-reading: `kilowatt_hours` (interval), `avg_true_power`, `avg_voltage`, `cost`, `timestamp`
- `GET /customers?customers_only=true&reading_details=false` — lightweight customer list
- `GET /customer/{code}` — single customer lookup (POST only? returns 405 on GET)
- `POST /transaction/` — credit customer meter balance
- Auth: `Authentication-Token` header (obtain from SparkCloud dashboard → Users → Payment Gateway → View Credentials)
- Parquet file access via session auth: `GET /history/list.json` → `GET /history/download/{filename}`
- **No separate historical readings endpoint** — historical data only available via daily parquet files
- No rate limiting observed on TC v0 API

### CC → SparkMeter Customer Sync (`sparkmeter_customer.py`)

1PDB is the authority for customer creation. When a customer is registered in CC
(single or bulk import), `sparkmeter_customer.py` pushes the customer to SparkMeter:

| Platform | Sites | Endpoint | Required | Notes |
|----------|-------|----------|----------|-------|
| Koios v1 | All LS except MAK; all BN | `POST /api/v1/customers` | `name`, `code`, `service_area_id` | Works without meter |
| ThunderCloud v0 | MAK | `POST /api/v0/customer/` | `serial`, `code`, `name`, `meter_tariff_name` | Requires meter serial |

- **Multi-country credentials**: Resolved per-country: `KOIOS_MANAGE_API_KEY_{CC}` → `KOIOS_API_KEY_{CC}` → `KOIOS_API_KEY`.
  - **LS**: Uses the read key (`KOIOS_API_KEY` / `KOIOS_API_SECRET`), which has customer creation access.
  - **BN**: Uses dedicated manage key (`KOIOS_MANAGE_API_KEY_BN` / `KOIOS_MANAGE_API_SECRET_BN`).
- **ThunderCloud**: If no meter serial at registration time, sync is deferred until meter assignment.
- **Name updates**: Neither platform supports customer name updates via API. Name drift must be corrected manually or via the audit/fix scripts in `scripts/ops/`.
- **Service area IDs**:
  - LS: Most sites share `e3015e87-...`; MAS uses `e6efc982-...`.
  - BN: GBO = `de00dfbf-...`; SAM = `43a81ea8-...`.

Integration points:
- `registration.py` → calls `create_sparkmeter_customer()` after 1PDB commit (single + bulk)
- `meter_lifecycle.py` → calls `create_sparkmeter_customer()` on meter assignment if customer doesn't exist in SM yet

### CC → SparkMeter Credit Pipe (`sparkmeter_credit.py`)
Routes credits by site code:
- **MAK/LAB** → ThunderCloud v0 `POST /transaction/` (requires customer UUID lookup first)
- **All other sites** → Koios v1 `POST /payments` with `customer_code` (single call)
- Integrated into `payments.py` (webhook + manual) and `crud.py` (generic create)

**Credentials (Koios payments):** Uses `KOIOS_WRITE_API_KEY_{CC}` / `KOIOS_WRITE_API_SECRET_{CC}` with fallback to global `KOIOS_WRITE_API_KEY` / `KOIOS_WRITE_API_SECRET`, then to `KOIOS_API_KEY` / `KOIOS_API_SECRET`. **Customer creation** may use the read/manage keys (`KOIOS_MANAGE_*` / `KOIOS_API_*`); **posting payments** requires write-capable keys. If the write pair is missing, wrong, or read-only, Koios returns 401/403 — **1PDB still records the payment first** (see ordering below).

**RCA — “CC updated but Koios didn’t credit” (recurring risk):**

1. **Commit order** — `payments.record` and `record_payment_kwh` **commit 1PDB before** calling SparkMeter. If Koios or ThunderCloud fails, **1PDB is already the source of truth for the portal balance**; SparkMeter is a **best-effort push**. There is **no automatic rollback** or retry queue today.
2. **Misclassified success (fixed 2026-04)** — `_koios_credit` previously treated any JSON body without `errors[]` as success, **without checking HTTP status**. Responses like **401/403** with `{"detail": "..."}` (FastAPI style) could be marked **success** while Koios never credited. The client now **requires HTTP 2xx** and treats non-JSON / error bodies as failure.
3. **Wrong or read-only API key** — Write key must match the country/org. Session log history: a **read-only** key was used for imports; write must be set for `/api/v1/payments`.
4. **customer_code mismatch** — Koios matches `customer_code` to the account string (e.g. `0252SHG`). If 1PDB account ≠ SparkMeter code (typo, drift), Koios returns an error (now surfaced).
5. **Network / timeout** — `API_TIMEOUT` (90s) or connection errors → `CreditResult(success=False)`; check server logs (`cc-api.sm-credit`).
6. **No retry** — A single transient failure leaves **1PDB credited, Koios not** until someone **re-posts** (manual Koios) or a future **retry job** exists.

**Operational checks after a suspected failure:** On the Record Payment success panel, read `sm_credit.success` and `sm_credit.error`. On the server: `journalctl -u 1pdb-api --lines 200 | grep -i koios`. Confirm `KOIOS_WRITE_API_KEY` / `KOIOS_WRITE_API_SECRET` for **LS** on the host that runs the Lesotho API.

**SMS mirror path (`/api/sms/incoming`):** Before 2026-04, this handler inserted into `transactions` **without** calling SparkMeter — so M-PESA rows could appear in CC/1PDB while Koios showed nothing. It now schedules `credit_sparkmeter` after commit (see `ingest.py`). Logs: `SMS path SM credit OK` / `SMS path SM credit failed`. Account selection uses the M-Pesa **Remark** (customer account pattern) when it matches `accounts`; otherwise the payer **phone** → customer lookup; phone-only matches notify Customer Care via the WhatsApp bridge. **Retroactive checks** against old misroutes require log lines with full SMS body or a gateway archive — see `scripts/ops/reconcile_sms_misroutes_from_logs.py`.

## ARPU Methodology

Both quarterly (`/api/om-report/arpu`) and monthly (`/api/om-report/monthly-arpu`) ARPU endpoints use:
- **Revenue**: Sum of `[transaction amount]` from `tblaccounthistory1` per period
- **Customers**: Cumulative distinct account numbers that have ever transacted up through the period (monotonically increasing)
- **ARPU**: Revenue / Cumulative Customers
- **Per-site breakdown**: Account numbers parsed via last 3 characters → site code mapping

## Multi-Country Architecture (Decision: 2026-02-18)

**Approach: Separate country backends, unified frontend.**

Each country gets its own PostgreSQL + FastAPI instance (same codebase, different config).
The frontend at cc.1pwrafrica.com is the integration layer — country selector at login,
all API calls routed to the selected country's backend.

```
cc.1pwrafrica.com (single React app)
  ├── Lesotho API  → cc-api-ls (1PDB-LS, LSL, M-PESA, Koios + ThunderCloud)
  ├── Benin API    → cc-api-bn (1PDB-BN, XOF, MTN MoMo, Koios Nova)
  └── Zambia API   → cc-api-zm (1PDB-ZM, ZMW, Airtel/MTN, TBD metering)
```

**Rationale**: Currency, payment pipelines (SMS format, mobile money provider, settlement),
tariff models, and data sovereignty requirements differ per country. A single multi-tenant
DB would require multi-currency everywhere and fragile payment multiplexing. Separate
backends keep each country clean; cross-country analytics happen in the frontend by
fanning out to N APIs and normalizing to USD.

**What's shared**: Frontend codebase, authentication system (employees can have
multi-country access), the codebase itself (deployed per-country with config).

**What's separate**: Database, API instance, payment pipeline, SparkMeter/meter platform
integration, SMS gateway.

**Onboarding a new country** (e.g. Zambia): follow `docs/sop-add-new-country.md` — `country_config.py`, dedicated DB + systemd service + Caddy route, frontend `COUNTRY_ROUTES`, Koios/org keys, SMS/payment parsers, and per-country WhatsApp bridge env (`CC_BRIDGE_NOTIFY_URL_<CC>`).

## Common Pitfalls

### 1. TypeScript Strict Mode
The frontend uses strict TypeScript. Unused imports/variables cause build failures. Always run `npx tsc -b --noEmit` before pushing.

### 2. Linux GitHub Actions Deploys
- Both deploy jobs run on GitHub-hosted Linux runners.
- Backend deploy syncs `acdb-api/` to `/opt/cc-portal/backend/` and restarts
  `1pdb-api` / `1pdb-api-bn`.
- Do not reintroduce Windows runner or `robocopy` assumptions into current docs
  or deployment guidance unless the live workflow changes.

### 3. Caddy Path Routing
API paths are proxied to the Linux-hosted FastAPI backend (`localhost:8100` and
country-specific peers). All other paths serve static frontend files with SPA
fallback. If you add new API routes, ensure the proxy layer still exposes them.

### 4. 1PDB Ownership
`1PDB` is the live production datastore. Avoid ad-hoc schema edits outside the
canonical migration/runtime flow. Legacy `acdb-*` names in repo paths are
historical only and should not be treated as evidence of an Access / ODBC stack.

---

## Related System: uGridPlan

| Item | Value |
|------|-------|
| **Repo** | [uGridPlan](https://github.com/onepowerLS/uGridPlan) |
| **Local path** | `/Users/mattmso/Dropbox/AI Projects/uGridPlan map_v3/` |
| **Portal** | https://ugp.1pwrafrica.com (prod), https://dev.ugp.1pwrafrica.com (staging) |

**Integration points**: O&M tickets (CC → uGridPlan), customer sync (uGridPlan → CC), shared O&M analytics, email notifications.

**Key rule**: No shared code. All integration via HTTP API calls. `1PDB` is the
single source of truth behind the CC API.

---

## Related Documentation

| Document | Content |
|----------|---------|
| `README.md` | Architecture overview, auto-deploy, quick start |
| `docs/whatsapp-customer-care.md` | Full WhatsApp bridge documentation, infrastructure, troubleshooting |
| `docs/sop-add-new-country.md` | SOP: adding a new country (DB, API, frontend, bridge, deploy) |
| `docs/ops/manual-adjustment-sms-discrepancies.md` | Team instructions: manual Koios + 1PDB corrections after SMS misallocations |
| `docs/ops/bn-sms-1pdb-gap.md` | Benin: CC API is ready; SMSComms-BN PHP must mirror to `/api/bn/sms/incoming` (not in this repo) |
| `docs/ops/sms-gateway-cpanel-deploy.md` | **LS vs BN** manual cPanel deploy, archive-before-overwrite, two hosts |
| `docs/credentials-and-secrets.md` | **Where credentials live** (GitHub secrets, server `.env`, AWS, related repos)—nothing secret in git |
| `docs/inter-repo-credentials.md` | **Inter-repo credential map** (same doc copied in 1PDB, SMSComms, uGridPlan, om-portal, ingestion_gate, onepwr-aws-mesh, etc.) |
| In-app **Help** (`/help`) | User guide: bilingual EN/FR body copy in `frontend/src/pages/helpSections.tsx`; UI chrome in `i18n/*/help.json`. Use **FR** toggle for full translation. |
| In-app **Tutorial** (`/tutorial`) | UX onboarding: orientation plus workflow walkthroughs; copy in `i18n/*/tutorial.json`, routes in `pages/tutorialWorkflows.ts`. |
| `SESSION_LOG.md` | AI session handoffs (read recent entries) |

---

## AI Session Protocol

### Why This Exists
AI assistants experience **context degradation** ("senescence") in long conversations. This protocol combats that by:
1. **Orientation docs** (CONTEXT.md, SESSION_LOG.md) - read at conversation start
2. **Session handoffs** - write at conversation end to pass knowledge to next session
3. **Self-improvement feedback** - each session notes gaps in the protocol

### At Conversation Start
1. Read this file (CONTEXT.md)
2. Read last 2 entries in SESSION_LOG.md
3. Briefly acknowledge orientation before proceeding

### During Conversation
- Journal major completions to SESSION_LOG.md
- Note if you have to re-discover something you should know (senescence signal)

### At Conversation End
Write SESSION_LOG.md entry with:
- What was done
- Key decisions
- What next session should know
- Protocol feedback (what was missing from docs?)
