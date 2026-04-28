# 1PWR Customer Care Portal — Context

> **Purpose**: Context for developers and AI assistants working inside
> `acdb-api/`, the main backend application area of the CC portal.

## What This Project Is

The **1PWR Customer Care (CC) Portal** is the application layer over `1PDB` for
customer operations, billing workflows, reporting, and O&M-facing support.

**Live URL**: https://cc.1pwrafrica.com

**Important**: The directory name `acdb-api/` is legacy naming from the old
Access/ACCDB era. The live system is now `1PDB`-backed and Linux-hosted.

## Architecture

- **Backend**: FastAPI (Python), typically served on port `8100`
- **Frontend**: React + TypeScript + Vite + Tailwind CSS (`acdb-api/frontend/`)
- **Database**: `1PDB` PostgreSQL
- **Hosting**: Linux CC stack behind Caddy
- **Legacy status**: ACCDB / Windows / `pyodbc` assumptions are deprecated

### Backend Modules

| Module | Description |
|--------|-------------|
| `customer_api.py` | Main FastAPI app with mounted routers and app setup |
| `auth.py` / `db_auth.py` | Employee authentication and auth storage |
| `crud.py` | Customer and meter CRUD workflows |
| `om_report.py` | O&M analytics and financial reporting |
| `exports.py` | Data export functionality |
| `commission.py` | Customer commissioning workflow |
| `contract_gen.py` | Contract document generation |
| `mutations.py` | Audit trail for data changes |
| `admin.py` | Roles and permissions administration |
| `pr_lookup.py` | Payment reference lookup |
| `balance_engine.py` | Priority-aware kWh balance engine (SM/1M source primacy via `accounts.billing_meter_priority` + `system_config(key='billing_meter_priority')`); also exposes `get_balance_kwh_what_if` for the migration test |
| `billing_priority.py` | `GET`/`PATCH` `/api/billing-priority(/account)` for ops to flip primacy per-account or fleet-wide; audited |
| `relay_control.py` | CC -> AWS IoT relay command channel (`POST /api/meters/{thing}/relay`, `POST /api/meters/relay-ack`) plus `maybe_auto_open_relay` hook gated by `RELAY_AUTO_TRIGGER_ENABLED` env. See [`docs/ops/1meter-billing-migration-protocol.md`](../docs/ops/1meter-billing-migration-protocol.md) |

### Frontend Pages

Login, Dashboard, Customer Data, Customer Detail, New Customer Wizard,
Commission Customer, Assign Meter, Financial, Mutations, O&M Report, Export,
Tariff Management, Sync, Tables, Admin Roles, and My Profile.

## Data Ownership

- `1PDB` owns canonical customer, meter, billing, and ingestion data.
- `1PWR CC` owns the portal/API workflows over that data.
- Legacy ACCDB artifacts in this repo should be treated as historical material,
  not as the active runtime contract.
- Archived ACCDB-era helper scripts now live under `legacy/accdb/` so they are
  preserved without staying in the active backend tree.

## O&M Integration

This portal plays two roles in the O&M system:

### 1. Employee Authentication Provider

The O&M portal at [om.1pwrafrica.com](https://om.1pwrafrica.com) proxies
`/api/auth/*` requests to this backend so employees can use the same credentials
across systems.

- **Endpoint**: `POST /api/auth/employee-login`
- **Response**: JWT token used by the O&M portal for session management
- **Runtime**: Linux-hosted CC backend over HTTP

### 2. O&M Financial Reporting

The `om_report.py` module provides quarterly operational metrics:

- Customer statistics per site
- Consumption per quarter per site
- Revenue per site per quarter
- Generation vs consumption comparisons

- **Endpoint family**: `/api/om-report/*`
- **Data source**: `1PDB` via the CC backend

### O&M Data Flow

```text
O&M Portal (om.1pwrafrica.com)
  ├─ Auth requests       → CC backend (this repo)
  ├─ Ticket / O&M data   → uGridPlan backend
  └─ Financial reports   → CC backend (`om_report.py`)
```

## Related Repositories

| Repo | Role | URL |
|------|------|-----|
| [1PDB](https://github.com/onepowerLS/1PDB) | Canonical schema, ingestion, runtime timers, and repair scripts | `1PDB` |
| [om-portal](https://github.com/onepowerLS/om-portal) | Standalone O&M frontend that proxies auth and reporting to CC | `om.1pwrafrica.com` |
| [uGridPlan](https://github.com/onepowerLS/uGridPlan) | O&M ticketing backend and planning tool | `ugp.1pwrafrica.com` |

## Hosting and Deployment

- **Frontend path**: `/opt/cc-portal/frontend/`
- **Backend path**: `/opt/cc-portal/backend/`
- **Backend services**: `1pdb-api` and `1pdb-api-bn`
- **Deploy workflow**: `.github/workflows/deploy.yml`

Current deployments are Linux-based GitHub Actions deploys. If you encounter
Windows/ACCDB instructions elsewhere in the repo, treat them as legacy unless
explicitly marked otherwise.
