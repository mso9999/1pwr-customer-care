# 1PWR Customer Care System

Customer care and O&M support system for 1PWR Africa minigrids. Provides WhatsApp-based ticket management and a customer database API backed by the ACDB (Access Customer Database).

## Architecture

The system runs across three EC2 instances:

| Component | Instance | URL |
|-----------|----------|-----|
| WhatsApp Bridge | Linux EC2 | N/A (WhatsApp protocol) |
| ACDB Customer API | Windows EC2 | https://cc.1pwrafrica.com |
| uGridPlan (O&M UI) | uGridPlan EC2 | https://ugp.1pwrafrica.com |

Communication between components is via HTTP -- no shared code or databases.

## Components

### `whatsapp-bridge/`

Node.js service using the Baileys library to connect to WhatsApp. Handles:
- Incoming customer messages → automatic ticket creation
- Technician dispatching and acknowledgment
- Ticket lifecycle (open → acknowledged → resolved)
- Customer lookup via ACDB API

**Deployment**: Runs as a systemd service on the Linux EC2 instance.

### `acdb-api/`

FastAPI service that wraps the ACDB Access database (`.accdb`). Provides:
- Customer lookup and management
- Billing and payment data
- Tariff management
- Revenue, consumption, and customer statistics (used for O&M reporting and ARPU)
- Commission calculations
- Contract generation
- uGridPlan sync endpoint

**Deployment**: Runs on the Windows EC2 instance (requires Access ODBC driver).

### `docs/`

Comprehensive documentation for the system architecture, deployment, and troubleshooting.

## Quick Start

### WhatsApp Bridge
```bash
cd whatsapp-bridge
npm install
node whatsapp-customer-care.js
```

### ACDB API
```bash
cd acdb-api
pip install -r requirements.txt
uvicorn customer_api:app --host 0.0.0.0 --port 8000
```

## Related Repositories

- **[uGridPlan](https://github.com/mso9999/uGridPlan)** -- Minigrid planning tool with O&M dashboard that consumes ACDB API data
