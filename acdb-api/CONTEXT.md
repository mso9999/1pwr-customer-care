# 1PWR Customer Care Portal — Context

> **Purpose**: This file provides context for AI assistants and developers working on the Customer Care system.

## What This Project Is

The **1PWR Customer Care (CC) Portal** is a customer management system for 1PWR Africa's minigrid operations. It handles customer onboarding, billing, contracts, meter assignment, and operational reporting.

**Live URL**: https://cc.1pwrafrica.com

## Architecture

- **Backend**: FastAPI (Python), runs on port 8100
- **Frontend**: React + TypeScript + Vite + Tailwind CSS
- **Database**: ACDB (Access-based Customer Database), accessed via `pyodbc`
- **Server**: Linux EC2 at `13.244.104.137` (shared with O&M portal)

### Backend Modules

| Module | Description |
|--------|-------------|
| `customer_api.py` | Main FastAPI app with customer CRUD endpoints |
| `auth.py` / `db_auth.py` | Employee authentication (JWT-based) |
| `crud.py` | Database operations for customers, meters, contracts |
| `om_report.py` | O&M quarterly reports — revenue, consumption, customer stats per site |
| `exports.py` | Data export functionality |
| `commission.py` | Customer commissioning workflow |
| `contract_gen.py` | Contract document generation |
| `mutations.py` | Audit trail for data changes |
| `admin.py` | Admin operations (roles, permissions) |
| `pr_lookup.py` | Payment reference lookup |

### Frontend Pages

Login, Dashboard, Customer Data, Customer Detail, New Customer Wizard, Commission Customer, Assign Meter, Financial, Mutations, O&M Report, Export, Tariff Management, Sync, Tables, Admin Roles, My Profile.

## O&M Integration

This portal plays two roles in the O&M system:

### 1. Employee Authentication Provider

The O&M portal at [om.1pwrafrica.com](https://om.1pwrafrica.com) proxies `/api/auth/*` requests to this backend. Employees log into the O&M portal using the same credentials as this portal.

- **Endpoint**: `POST /api/auth/employee-login` (employee number + password)
- **Response**: JWT token used by the O&M portal for session management
- **Caddy proxy**: `om.1pwrafrica.com/api/auth/*` → `172.31.2.39:8100`

### 2. O&M Financial Reporting

The `om_report.py` module provides quarterly operational metrics:
- Customer statistics per site (active, inactive, growth)
- Consumption per quarter per site
- Revenue per site per quarter
- Generation vs consumption comparison

- **Endpoint**: `/api/om-report/*`
- **Caddy proxy**: `om.1pwrafrica.com/api/om-report/*` → `172.31.2.39:8100`
- **Data source**: ACDB customer database (same DB used by this portal)

### O&M Data Flow

```
O&M Portal (om.1pwrafrica.com)
  ├─ Auth requests      → CC backend (this repo, port 8100)
  ├─ Ticket/O&M data    → uGridPlan backend (15.240.40.213:8017)
  └─ Financial reports   → CC backend (this repo, om_report.py)
```

## Related Repositories

| Repo | Role | URL |
|------|------|-----|
| [om-portal](https://github.com/onepowerLS/om-portal) | Standalone O&M frontend (React SPA). Proxies auth and financial requests to this backend. | `om.1pwrafrica.com` |
| [uGridPlan](https://github.com/onepowerLS/uGridPlan) | O&M backend API (ticket storage, statistics). Also a minigrid network planning tool. | `ugp.1pwrafrica.com` |

## Hosting

- **Server**: Linux EC2 at `13.244.104.137` (internal: `172.31.2.39`)
- **Backend port**: 8100
- **Frontend**: Served by Caddy at `cc.1pwrafrica.com`
- **Shared server**: The O&M portal frontend is also hosted on this machine

## Deployment

Currently deployed manually (no GitHub Actions workflow). Backend and frontend are deployed via SSH/rsync to the server.
