# 1PWR Customer Care System

Customer care and O&M support system for 1PWR Africa minigrids. Provides a web-based customer care portal, WhatsApp-based ticket management, financial analytics, and a customer database API backed by the ACDB (Access Customer Database).

**Portal**: https://cc.1pwrafrica.com

## Architecture

The system runs across three EC2 instances in AWS Africa (Cape Town) / af-south-1, all on the same VPC (`172.31.0.0/16`):

| Component | Instance | URL / Access |
|-----------|----------|--------------|
| CC Portal Frontend | Linux EC2 (`13.244.104.137`) | https://cc.1pwrafrica.com (served by Caddy) |
| ACDB Customer API (backend) | Windows EC2 (`172.31.2.39`) | Proxied via Caddy at `/api/*` |
| WhatsApp Bridge | Linux EC2 | N/A (WhatsApp protocol via Baileys) |
| uGridPlan (O&M UI) | uGridPlan EC2 (`15.240.40.213`) | https://ugp.1pwrafrica.com |

### How it fits together

```
                           ┌─────────────────────────┐
                           │    cc.1pwrafrica.com     │
                           │    (Caddy on Linux EC2)  │
                           │                          │
                           │  Static files ──► /opt/cc-portal/frontend/
                           │  /api/*       ──► 172.31.2.39:8100 (Windows)
                           └─────────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
   │  React Frontend  │     │  FastAPI Backend │     │  WhatsApp Bridge │
   │  (Vite + TS)     │     │  (customer_api)  │     │  (Node.js)       │
   │                  │     │                  │     │                  │
   │  - Dashboard     │     │  - ACDB via ODBC │     │  - Baileys API   │
   │  - O&M Report    │     │  - Customer CRUD │     │  - OpenClaw AI   │
   │  - Financial     │     │  - O&M analytics │     │  - Ticket creation│
   │  - Customers     │     │  - ARPU / revenue│     │  via uGridPlan   │
   │  - Tariffs       │     │  - Tariffs       │     │                  │
   │  - Mutations     │     │  - Auth / roles  │     │                  │
   └─────────────────┘     └─────────────────┘     └─────────────────┘
```

Communication between components is via HTTP -- no shared code or databases.

## Link to uGridPlan

This system and [uGridPlan](https://github.com/mso9999/uGridPlan) are **separate applications** that integrate at the API level:

| Integration Point | Direction | Mechanism |
|-------------------|-----------|-----------|
| **O&M Tickets** | CC → uGridPlan | WhatsApp bridge creates tickets via `POST /om/tickets` on uGridPlan API |
| **Customer Lookup** | uGridPlan → CC | uGridPlan can sync customer data from the ACDB API (`/api/customers/*`) |
| **O&M Analytics** | Shared data | Both systems display O&M metrics; uGridPlan has the O&M dashboard, CC portal has the O&M Report page |
| **Notifications** | uGridPlan → Email | Ticket creation triggers email dispatch via uGridPlan's SMTP integration |

**Key rule**: Neither system imports code from the other. All integration is via HTTP API calls. The ACDB (Access database on Windows EC2) is the single source of truth for customer and billing data.

## Components

### `acdb-api/`

FastAPI service that wraps the ACDB Access database (`.accdb`). Provides:
- Customer lookup and management
- Billing and payment data
- Tariff management
- Revenue, consumption, and customer statistics (O&M reporting and ARPU)
- Financial analytics (monthly/quarterly ARPU, revenue by site)
- Commission calculations
- Contract generation
- uGridPlan sync endpoint

**Deployment**: Runs on the Windows EC2 instance (requires Access ODBC driver). Auto-deployed via GitHub Actions.

### `acdb-api/frontend/`

React + TypeScript + Vite single-page application. Key pages:
- **Dashboard** -- customer count, MWh, revenue, site overview
- **O&M Report** -- quarterly operations & maintenance analytics
- **Financial** -- ARPU time series (monthly + quarterly), revenue by site, per-site breakdown
- **Customers** -- search, view, edit customer records
- **Tariffs** -- tariff management
- **Mutations** -- audit log of data changes

**Deployment**: Built on GitHub-hosted runner, deployed to Linux EC2 where Caddy serves static files.

### `whatsapp-bridge/`

Node.js service using the Baileys library to connect to WhatsApp. Handles:
- Incoming customer messages → AI classification → automatic ticket creation
- Technician dispatching and acknowledgment
- Ticket lifecycle (open → acknowledged → resolved)
- Customer lookup via ACDB API

**Deployment**: Runs as a PM2-managed process on the Linux EC2 instance.

### `docs/`

Comprehensive documentation for the system architecture, deployment, and troubleshooting.

## Auto-Deploy (CI/CD)

Pushing to `main` triggers a GitHub Actions workflow (`.github/workflows/deploy.yml`) with two parallel jobs:

| Job | Runner | What it does |
|-----|--------|--------------|
| `deploy-frontend` | GitHub-hosted `ubuntu-latest` | `npm ci && npm run build`, then `rsync` to Linux EC2 `/opt/cc-portal/frontend/` |
| `deploy-backend` | Self-hosted Windows runner | `robocopy` Python files to `C:\acdb-customer-api\`, `pip install`, restart `ACDBCustomerAPI` scheduled task |

### Caddy Configuration (Linux EC2)

Caddy reverse-proxies API requests to the Windows EC2 and serves the frontend as static files:

```
cc.1pwrafrica.com {
    handle /api/*        { reverse_proxy 172.31.2.39:8100 }
    handle /health       { reverse_proxy 172.31.2.39:8100 }
    handle /customers/*  { reverse_proxy 172.31.2.39:8100 }
    handle /sites        { reverse_proxy 172.31.2.39:8100 }
    handle {
        root * /opt/cc-portal/frontend
        try_files {path} /index.html
        file_server
    }
}
```

### GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `EC2_SSH_KEY` | SSH private key for Linux EC2 (contents of `EOver.pem`) |
| `EC2_LINUX_HOST` | Linux EC2 public IP (`13.244.104.137`) |

### Windows Runner

The self-hosted runner on the Windows EC2 runs as `LocalSystem` to have permissions for service management:
```powershell
sc.exe config "actions.runner.mso9999-1pwr-customer-care.EC2AMAZ-RP132EU" obj= "LocalSystem"
```

## Quick Start

### ACDB API (backend)
```bash
cd acdb-api
pip install -r requirements.txt
uvicorn customer_api:app --host 0.0.0.0 --port 8100
```

### CC Portal (frontend)
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

- **[uGridPlan](https://github.com/mso9999/uGridPlan)** -- Minigrid planning tool with O&M dashboard. CC system creates tickets via uGridPlan API and syncs customer data from the ACDB API. See "Link to uGridPlan" section above.
