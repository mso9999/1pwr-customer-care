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

**Domain**: Rural minigrids (solar+battery) in Lesotho, operated by 1PWR Africa / OnePower Lesotho.
**Users**: 1PWR operations staff, finance team, customer care agents.
**Data Source**: ACDB (Microsoft Access `.accdb` database) containing ~1,300+ customers across 10+ sites.

## Architecture

```
cc.1pwrafrica.com
       │
       ▼
┌──────────────────────────────────────────────────┐
│  Linux EC2 (13.244.104.137) - Caddy              │
│                                                    │
│  Static files → /opt/cc-portal/frontend/           │
│  /api/*       → reverse_proxy 172.31.2.39:8100     │
│  /health      → reverse_proxy 172.31.2.39:8100     │
│  /customers/* → reverse_proxy 172.31.2.39:8100     │
│  /sites       → reverse_proxy 172.31.2.39:8100     │
└──────────────────────────────────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────┐         ┌─────────────────────────┐
│ WhatsApp     │         │ Windows EC2             │
│ Bridge       │         │ (172.31.2.39)           │
│ (Node.js)    │         │                         │
│ PM2-managed  │         │ FastAPI on port 8100    │
│              │         │ ACDB via pyodbc/ODBC    │
│ Creates O&M  │         │ Scheduled Task:         │
│ tickets via  │         │   ACDBCustomerAPI       │
│ uGridPlan API│         │ Runner: LocalSystem     │
└──────────────┘         └─────────────────────────┘
```

All three EC2 instances are in AWS af-south-1, same VPC (`172.31.0.0/16`).

## Key Files

### Backend (`acdb-api/`)

| File | Purpose |
|------|---------|
| `customer_api.py` | Main FastAPI app -- mounts all routers, CORS, auth |
| `om_report.py` | O&M analytics endpoints: customer stats, consumption, sales, ARPU |
| `crud.py` | Customer CRUD operations |
| `auth.py` | Authentication (employee login, JWT tokens) |
| `db_auth.py` | Auth database (SQLite for user accounts) |
| `middleware.py` | Auth middleware, role-based access |
| `models.py` | Pydantic models |
| `tariff.py` | Tariff management endpoints |
| `mutations.py` | Data mutation audit log |
| `exports.py` | Data export endpoints |
| `stats.py` | Dashboard statistics |
| `commission.py` | Commission calculations |
| `contract_gen.py` | Contract PDF generation |
| `sync_ugridplan.py` | Sync data to uGridPlan |
| `requirements.txt` | Python dependencies |

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

## ARPU Methodology

Both quarterly (`/api/om-report/arpu`) and monthly (`/api/om-report/monthly-arpu`) ARPU endpoints use:
- **Revenue**: Sum of `[transaction amount]` from `tblaccounthistory1` per period
- **Customers**: Cumulative distinct account numbers that have ever transacted up through the period (monotonically increasing)
- **ARPU**: Revenue / Cumulative Customers
- **Per-site breakdown**: Account numbers parsed via last 3 characters → site code mapping

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
