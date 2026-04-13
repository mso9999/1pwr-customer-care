# 1PWR Customer Care System

Customer care, billing, and O&M support portal for 1PWR Africa minigrids.

**Portal**: https://cc.1pwrafrica.com

## Status

This repository still carries legacy path names such as `acdb-api/`, but the
production system is now centered on `1PDB` (PostgreSQL), not Microsoft Access.

- `1PDB` is the canonical source of truth for customer, meter, billing, and
  ingestion data.
- `1PWR CC` is the portal and API layer over that data.
- The old ACCDB / Windows-hosted workflow is deprecated and should be treated as
  historical or forensic material only.

## Architecture

| Component | Runtime | Role |
|-----------|---------|------|
| CC Portal Frontend | Linux EC2 + Caddy | Serves `cc.1pwrafrica.com` |
| CC Backend API | Linux EC2 + FastAPI | Portal, auth, reporting, and workflow API |
| Canonical datastore | `1PDB` PostgreSQL | Source of truth for customer, meter, and billing data |
| WhatsApp Bridge | Linux EC2 + PM2 | Customer care automation and ticket workflow |
| uGridPlan | Separate EC2 + FastAPI/React | O&M ticketing and planning system |

### How it fits together

```text
Customer Browser / WhatsApp
            |
            v
   +--------------------------+
   |    cc.1pwrafrica.com     |
   |  Caddy + React frontend  |
   +--------------------------+
            |
            v
   +--------------------------+
   |   FastAPI backend        |
   |   (this repo)            |
   |   /opt/cc-portal/backend |
   +--------------------------+
            |
            v
   +--------------------------+
   |        1PDB              |
   |   PostgreSQL + ingest    |
   |   runtime / timers       |
   +--------------------------+

WhatsApp bridge runs alongside the CC stack, looks up customer context via the
CC API, and creates O&M tickets in uGridPlan over HTTP.
```

**Key rule**: No shared code between repos. Integration happens over HTTP APIs.
`1PDB` is the single source of truth; `1PWR CC` is the application layer over it.

## Repo Layout

- `acdb-api/` — main FastAPI backend. The directory name is legacy.
- `acdb-api/frontend/` — React + TypeScript + Vite portal UI.
- `whatsapp-bridge/` — WhatsApp / Baileys automation.
- `docs/` — architecture, ops, and troubleshooting documentation.
- `docs/credentials-and-secrets.md` — **where to fetch credentials** (GitHub Actions secrets, EC2 `.env` paths; CC-focused).
- `docs/inter-repo-credentials.md` — **same org-wide map** committed in 1PDB, SMSComms, uGridPlan, om-portal, ingestion_gate, onepwr-aws-mesh (cross-repo pointers only).

## Responsibilities

### `1PDB` owns

- Database schema and migrations
- Canonical customer, meter, billing, and reading state
- Ingestion jobs and normalization
- Backfills, repair scripts, and systemd timer/runtime behavior

### `1PWR CC` owns

- Employee authentication and RBAC
- Customer care portal UI
- Reporting, exports, contracts, and operational workflows
- WhatsApp customer support automation
- uGridPlan-facing application/API integration

### Legacy / deprecated

The following are no longer part of the active production architecture:

- ACCDB `.accdb` files as live operational data
- Windows EC2 assumptions for the CC backend
- Access ODBC / `pyodbc` as the current runtime contract
- Older ACCDB-era operational scripts such as `sync_accdb.ps1`,
  `compact_accdb.py`, and `import_meter_readings.py`

Those archived scripts are preserved under `legacy/accdb/` and are intentionally
kept out of the active backend deploy path.

## Components

### `acdb-api/`

FastAPI service over `1PDB`. Provides:

- Customer lookup and management
- Billing and payment workflows
- Tariff management
- Revenue, consumption, and customer statistics
- Financial analytics (monthly / quarterly ARPU, revenue by site)
- Commission, contract, and export workflows
- uGridPlan sync endpoints

**Deployment**: Auto-deployed to the Linux CC host via GitHub Actions.

### `acdb-api/frontend/`

React + TypeScript + Vite SPA. Key pages include:

- Dashboard
- O&M Report
- Financial analytics
- Customers
- Tariffs
- Mutations
- Admin roles

**Deployment**: Built on GitHub-hosted runners and deployed to
`/opt/cc-portal/frontend/`.

### `whatsapp-bridge/`

Node.js service using Baileys to connect to WhatsApp. Handles:

- Incoming customer messages -> AI classification -> reply
- Customer context lookup through the CC API
- Ticket creation in uGridPlan
- Group notifications and conversation tracking

**Deployment**: Runs as a PM2-managed process on the Linux CC host.

## Link to uGridPlan

This system and [uGridPlan](https://github.com/onepowerLS/uGridPlan) are
separate applications that integrate at the API level:

| Integration Point | Direction | Mechanism |
|-------------------|-----------|-----------|
| **O&M Tickets** | CC -> uGridPlan | WhatsApp bridge creates tickets via `POST /om/tickets` |
| **Customer Sync** | uGridPlan -> CC | uGridPlan syncs customer data from CC endpoints backed by `1PDB` |
| **O&M Analytics** | Shared | Both systems display O&M metrics sourced through CC / `1PDB` data |
| **Notifications** | uGridPlan -> Email | Ticket creation triggers uGridPlan notification flows |

## User guide (in-app)

End-user documentation lives in the portal at **https://cc.1pwrafrica.com/help** (sidebar → Help). It is maintained in `acdb-api/frontend/src/pages/helpSections.tsx` with English and French copy, and mirrored UI strings in `acdb-api/frontend/src/i18n/en/help.json` and `fr/help.json`. Switch **EN / FR** in the sidebar to translate the full manual (not only section titles).

## Auto-Deploy (CI/CD)

Pushing to `main` triggers `.github/workflows/deploy.yml` with two parallel jobs:

| Job | Runner | What it does |
|-----|--------|--------------|
| `deploy-frontend` | GitHub-hosted `ubuntu-latest` | `npm ci` + `npm run build`; rsync `dist/` to `/opt/cc-portal/frontend/` |
| `deploy-backend` | GitHub-hosted `ubuntu-latest` | rsync `acdb-api/` to `/opt/cc-portal/backend/` (excludes `frontend/`, `.env`, caches); `pip install -r requirements.txt` as `cc_api`; restarts `1pdb-api` and `1pdb-api-bn` |

**GitHub Actions secrets** (repo → Settings → Secrets and variables → Actions): `EC2_SSH_KEY` (private key for `ubuntu@` host), `EC2_LINUX_HOST` (hostname or IP). See `docs/credentials-and-secrets.md`.

**Pre-push check (frontend):** `cd acdb-api/frontend && npx tsc -b --noEmit` — the deploy fails if TypeScript does not compile.

The workflow then verifies:

- `https://cc.1pwrafrica.com/`
- `https://cc.1pwrafrica.com/api/health`
- `https://cc.1pwrafrica.com/api/bn/health`

### Host note

The current Linux host address is managed outside the repo (`EC2_LINUX_HOST`
secret / AWS inventory). Do not hardcode stale public IPs in new docs or scripts.

## Quick Start

### Backend

```bash
cd acdb-api
pip install -r requirements.txt
uvicorn customer_api:app --host 0.0.0.0 --port 8100
```

Typical local setup requires `DATABASE_URL` and any relevant service credentials. **Where those live in production and CI** is documented in `docs/credentials-and-secrets.md` and the shared `docs/inter-repo-credentials.md` (values are never committed).

### Frontend

```bash
cd acdb-api/frontend
npm install
npm run dev
```

### WhatsApp Bridge

```bash
cd whatsapp-bridge
npm install
node whatsapp-customer-care.js
```

## Related Repositories

- [1PDB](https://github.com/onepowerLS/1PDB) — canonical schema, ingestion, and runtime data stack
- [uGridPlan](https://github.com/onepowerLS/uGridPlan) — planning app and O&M ticketing backend
- [om-portal](https://github.com/onepowerLS/om-portal) — standalone O&M frontend
