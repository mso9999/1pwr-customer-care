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
**Data Source**: 1PDB (PostgreSQL 16) — migrated from ACCDB. ~1,400+ customers across 10+ sites (Lesotho).

## Architecture (Post-Migration)

```
cc.1pwrafrica.com
       │
       ▼
┌──────────────────────────────────────────────────┐
│  Linux EC2 (13.244.104.137) - Caddy              │
│                                                    │
│  Static files → /opt/cc-portal/frontend/           │
│  /api/*       → reverse_proxy localhost:8100       │
│  /health      → reverse_proxy localhost:8100       │
│  /customers/* → reverse_proxy localhost:8100       │
│  /sites       → reverse_proxy localhost:8100       │
│                                                    │
│  FastAPI backend (psycopg2 → PostgreSQL)           │
│  PostgreSQL 16 (1PDB - master of record)           │
│  systemd: 1pdb-api, 1pdb-import (timer)            │
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

The Linux EC2 runs everything: Caddy, FastAPI, PostgreSQL. No Windows EC2 dependency.

### Related Repos
- **onepowerLS/1PDB** — Database schema, migration scripts, data pipeline services
- **onepowerLS/SMSComms** — Lesotho SMS Gateway (sms.1pwrafrica.com)
- **onepowerLS/SMSComms-BN** — Benin SMS Gateway (smsbn.1pwrafrica.com)
- **onepowerLS/SMS-Gateway-APP** — Android Medic Gateway app (phones → PHP gateways)
- **onepowerLS/ingestion_gate** — Prototype meter IoT Lambda (DynamoDB)

## Key Files

### Backend (`acdb-api/`)

| File | Purpose |
|------|---------|
| `customer_api.py` | Main FastAPI app -- mounts all routers, psycopg2 pool, CORS, auth |
| `om_report.py` | O&M analytics endpoints: customer stats, consumption, sales, ARPU |
| `crud.py` | Customer CRUD operations (PostgreSQL information_schema introspection) |
| `auth.py` | Authentication (employee login, JWT tokens) |
| `db_auth.py` | Auth database (SQLite for user accounts) |
| `middleware.py` | Auth middleware, role-based access |
| `models.py` | Pydantic models |
| `tariff.py` | Tariff management endpoints (system_config table) |
| `mutations.py` | Data mutation audit log |
| `exports.py` | Data export endpoints |
| `stats.py` | Dashboard statistics |
| `commission.py` | Commission workflow + bulk status update |
| `registration.py` | Customer registration + Excel bulk import |
| `contract_gen.py` | Contract PDF generation |
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
| `whatsapp-bridge/whatsapp-customer-care.js` | WhatsApp CC bridge |
| `docs/whatsapp-customer-care.md` | Full system documentation |

## Deployment

### Auto-Deploy (Primary Method)
Push to `main` triggers GitHub Actions with two parallel jobs:
- **deploy-frontend**: GitHub-hosted runner → `npm ci && npm run build` → `rsync` to Linux EC2
- **deploy-backend**: Self-hosted Windows runner → `robocopy` → `pip install` → restart service

### Manual Access

| Target | Command |
|--------|---------|
| Linux EC2 (Caddy, Bridge) | `ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137` |
| Windows EC2 (ACDB API) | RDP via SSH tunnel: `ssh -i EOver.pem -L 3389:172.31.2.39:3389 -N ubuntu@13.244.104.137` |
| uGridPlan EC2 | `ssh -i EOver.pem ubuntu@13.244.104.137 "ssh -i ~/.ssh/uGridPLAN.pem -p 2222 ugridplan@15.240.40.213"` |

### Service Management

| Service | How to manage |
|---------|---------------|
| Caddy (frontend) | `ssh ubuntu@13.244.104.137 "sudo systemctl reload caddy"` |
| CC Bridge | `ssh ubuntu@13.244.104.137 "pm2 restart whatsapp-cc"` |
| ACDB API | Via deploy workflow, or RDP → `schtasks.exe /End /TN "ACDBCustomerAPI"` then `/Run` |

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

### Data Sources
| Source | Platform | Coverage | Ingestion |
|--------|----------|----------|-----------|
| Koios v2 historical | SparkMeter Cloud | KET, LSB, MAS, MAT, SEH, SHG, TLH (LS) + GBO, SAM (BN). RIB/TOS not yet operational. | `import_hourly.py` (incremental commits, `--no-skip` for gap-filling) + systemd timer |
| ThunderCloud parquet | SparkMeter on-prem | MAK | `import_thundercloud.py` (batch, ~1 day lag, `ON CONFLICT DO UPDATE` for gap-fill) |
| ThunderCloud v0 live | SparkMeter on-prem | MAK | `import_tc_live.py` (cumulative register diffs, non-lossy, 15-min intervals) |
| ThunderCloud web API | SparkMeter on-prem | MAK | `import_tc_transactions.py` (live transactions) |
| IoT / ingestion_gate | 1Meter prototype | MAK (3 meters) | Real-time Lambda → POST /api/meters/reading |
| SMS Gateway (LS) | M-PESA payments | All LS sites | sms.1pwrafrica.com mirrors to POST /api/sms/incoming |
| SMS Gateway (BN) | MTN MoMo payments | All BN sites | smsbn.1pwrafrica.com mirrors to POST /api/bn/sms/incoming |

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

### CC → SparkMeter Credit Pipe (`sparkmeter_credit.py`)
Routes credits by site code:
- **MAK/LAB** → ThunderCloud v0 `POST /transaction/` (requires customer UUID lookup first)
- **All other sites** → Koios v1 `POST /payments` with `customer_code` (single call)
- Integrated into `payments.py` (webhook + manual) and `crud.py` (generic create)

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

## Common Pitfalls

### 1. TypeScript Strict Mode
The frontend uses strict TypeScript. Unused imports/variables cause build failures. Always run `npx tsc -b --noEmit` before pushing.

### 2. Windows PowerShell in GitHub Actions
- `robocopy` exit codes 0-7 are success; 8+ are errors. Must handle explicitly.
- `pip` writes warnings to stderr that PowerShell treats as errors. Suppress with `2>$null`.
- The runner service runs as `LocalSystem` -- do not change this or service management breaks.

### 3. Caddy Path Routing
API paths (`/api/*`, `/health`, `/customers/*`, `/sites`) are proxied to Windows EC2. All other paths serve static frontend files with SPA fallback. If you add new API routes, ensure they match an existing `handle` block or add one to the Caddyfile.

### 4. Access Database
The ACDB is a live production database. Never modify schema. Read-only operations are safe; write operations go through the existing CRUD layer which handles ODBC connection pooling.

---

## Related System: uGridPlan

| Item | Value |
|------|-------|
| **Repo** | [uGridPlan](https://github.com/onepowerLS/uGridPlan) |
| **Local path** | `/Users/mattmso/Dropbox/AI Projects/uGridPlan map_v3/` |
| **Portal** | https://ugp.1pwrafrica.com (prod), https://dev.ugp.1pwrafrica.com (staging) |

**Integration points**: O&M tickets (CC → uGridPlan), customer sync (uGridPlan → CC), shared O&M analytics, email notifications.

**Key rule**: No shared code. All integration via HTTP API calls. ACDB is the single source of truth.

---

## Related Documentation

| Document | Content |
|----------|---------|
| `README.md` | Architecture overview, auto-deploy, quick start |
| `docs/whatsapp-customer-care.md` | Full WhatsApp bridge documentation, infrastructure, troubleshooting |
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
