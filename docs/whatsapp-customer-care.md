# WhatsApp Customer Care System

Automated WhatsApp-based customer care for 1PWR minigrids. Customers message the
CC phone via WhatsApp; an AI classifies the issue, looks up customer context
through the CC API, creates an O&M ticket in uGridPlan when needed, replies in
bilingual Sesotho/English, and notifies operations via WhatsApp group and email.

## Status

The active production architecture is Linux-hosted and `1PDB`-backed.

- Customer lookup now runs through the CC backend API backed by `1PDB`.
- The old Windows/ACCDB path is deprecated and should be treated as legacy.
- Some variable names and repo paths still use historical `acdb` naming, but
  they should point to the current CC API, not to a Windows host.

## 1. System Overview

### Architecture

```text
Customer WhatsApp
       |
       v
+------------------+     +---------------------------+     +------------------+
| CC Bridge        |---->| CC API                    |---->| 1PDB             |
| Linux host       |     | Linux FastAPI backend     |     | PostgreSQL       |
| Node.js/Baileys  |     | https://cc.../api         |     | source of truth  |
+------------------+     +---------------------------+     +------------------+
       |
       +---> OpenClaw AI (classify message, generate bilingual reply)
       |
       +---> uGridPlan API (create O&M ticket)
       |         |
       |         +---> Email notification
       |
       +---> WhatsApp group notification
       |
       v
Customer receives bilingual reply with ticket number
```

### End-to-End Message Flow

1. Customer sends a WhatsApp message to the CC phone.
2. The bridge receives it via Baileys.
3. The bridge filters duplicates, stale history, protocol messages, and echoes.
4. The bridge looks up customer context through the CC API (`1PDB`-backed).
5. OpenClaw classifies the message and drafts a bilingual reply.
6. If ticket-worthy, the bridge creates a ticket in uGridPlan.
7. uGridPlan dispatches notification workflows.
8. The bridge sends the customer reply and posts the tracker-group message.
9. Follow-up messages within the conversation window are appended to the existing ticket.

## 2. Infrastructure

### CC Linux Host

This host runs the public CC stack and the WhatsApp bridge.

| Component | Runtime |
|-----------|---------|
| `cc.1pwrafrica.com` | Caddy + static frontend |
| CC backend API | FastAPI services such as `1pdb-api` / `1pdb-api-bn` |
| WhatsApp bridge | PM2 |
| OpenClaw gateway | systemd / local service |

**Access**:

```bash
ssh -i "/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem" ubuntu@<current-cc-linux-host>
```

**Keys:** Use **`EOver.pem`** from **`/Users/mattmso/Dropbox/AI Projects/secrets`** (Dropbox-synced). Resolve `<current-cc-linux-host>` with **`aws ec2 describe-instances`** (see `CONTEXT.md`) or the `EC2_LINUX_HOST`
deploy secret. Do not rely on historical hardcoded public IPs in older docs.

### uGridPlan Host

uGridPlan remains the O&M ticketing backend and planning system.

| Item | Value |
|------|-------|
| Portal | `https://ugp.1pwrafrica.com` |
| Staging | `https://dev.ugp.1pwrafrica.com` |
| SSH | `ssh -p 2222 -i uGridPLAN.pem ugridplan@15.240.40.213` |

### Legacy Infrastructure

Older Windows ACCDB hosts may still exist for historical reference, but they
are not part of the active production design for customer lookup or billing.

## 3. WhatsApp CC Bridge

**Repo path**: `whatsapp-bridge/whatsapp-customer-care.js`

**Runtime**: Node.js with `@whiskeysockets/baileys`

### Configuration Notes

Typical bridge configuration includes:

```text
CC phone                = +266 58342168
UGRIDPLAN_API           = https://dev.ugp.1pwrafrica.com/api  (or production)
CC lookup API           = https://cc.1pwrafrica.com/api
AUTH_DIR                = /home/ubuntu/whatsapp-logger/baileys_auth_cc
STATE_FILE              = /home/ubuntu/whatsapp-logger/cc-state.json
CONV_FILE               = /home/ubuntu/whatsapp-logger/cc-conversations.json
CONVERSATION_WINDOW     = 30 minutes
AGENT_TIMEOUT           = 30 seconds
MSG_DEDUP_MAX           = 500
```

If the code still uses a legacy variable name such as `ACDB_API`, it should
still point at the current CC API, not a deprecated Windows host.

**CC API → bridge (`POST /notify`)**: Per-country env vars `CC_BRIDGE_NOTIFY_URL_<CC>` and `CC_BRIDGE_SECRET_<CC>` (e.g. `_BN`, `_ZM`) override the shared `CC_BRIDGE_NOTIFY_URL` / `CC_BRIDGE_SECRET`. Default port `BRIDGE_INBOUND_PORT` is often 3847. Deploy a separate bridge process (and tracker group JID) per country so each country’s Customer Care WhatsApp receives only its own alerts. See `docs/sop-add-new-country.md`.

### Anti-Spam / Message Filtering

The bridge should skip:

1. Its own messages
2. Status broadcasts
3. Bot-number echoes
4. Duplicate message IDs
5. Stale history sync
6. Protocol/system messages
7. Excessive repeated replies to the same sender

### AI Classification

OpenClaw returns structured fields such as:

- `needs_ticket`
- `category`
- `priority`
- `site_id`
- `fault_summary`
- `customer_reply`

Replies are bilingual: Sesotho first, then English.

### Ticket Creation Mapping

| Bridge field | Ticket field |
|--------------|--------------|
| `site_id` | `site_id` |
| `fault_summary` | `fault_description` |
| `priority` | `priority` |
| `category` | `equipment_category` |
| customer lookup result | `reported_by`, `customer_id` |

### PM2 Resilience

```bash
pm2 start whatsapp-customer-care.js \
  --name "whatsapp-cc" \
  --max-memory-restart 300M \
  --exp-backoff-restart-delay 1000 \
  --max-restarts 50 \
  --kill-timeout 10000

pm2 save
pm2 startup
```

## 4. Customer Lookup API

The bridge uses the CC backend API, which is backed by `1PDB`.

**Primary public base URL**: `https://cc.1pwrafrica.com/api`

**Typical internal health check**: `http://localhost:8100/health`

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check + DB connectivity |
| GET | `/customers/by-phone/{phone}` | Lookup by phone |
| GET | `/customers/by-id/{customer_id}` | Lookup by customer ID |
| GET | `/customers/by-account/{acct}` | Lookup by account number |
| GET | `/customers/search` | Search by name / location |
| GET | `/sites` | List sites / concessions |

### Runtime

- **Source file**: `acdb-api/customer_api.py`
- **Database**: `1PDB` PostgreSQL
- **Driver**: `psycopg2`
- **Hosting**: Linux CC host
- **Services**: `1pdb-api` and `1pdb-api-bn`

### Phone Number Normalization

Customer lookup should normalize phone numbers by:

1. Stripping non-digits
2. Removing country-code prefixes when appropriate
3. Stripping leading zeros
4. Matching the normalized local form

## 5. uGridPlan O&M Integration

The bridge creates tickets in uGridPlan over HTTP.

### Integration Points

- `POST /om/tickets` for corrective tickets
- uGridPlan handles downstream notification workflows
- CC and uGridPlan remain separate repos with API-level integration only

### Environment Targeting

| Setting | Staging | Production |
|---------|---------|------------|
| `UGRIDPLAN_API` | `https://dev.ugp.1pwrafrica.com/api` | `https://ugp.1pwrafrica.com/api` |

Switching between staging and production should be done by changing the
uGridPlan API target, not by reviving old ACCDB infrastructure.

## 6. Operations Guide

### Service Management

```bash
ssh -i "/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem" ubuntu@<current-cc-linux-host>
```

**Bridge**

```bash
pm2 list
pm2 logs whatsapp-cc --lines 30
pm2 restart whatsapp-cc
pm2 stop whatsapp-cc
pm2 save
```

**CC API**

```bash
sudo systemctl restart 1pdb-api
sudo systemctl restart 1pdb-api-bn
sudo journalctl -u 1pdb-api -f
sudo journalctl -u 1pdb-api-bn -f
```

**Caddy**

```bash
sudo systemctl reload caddy
sudo systemctl status caddy
```

### Log Locations

| Component | Where to look |
|-----------|---------------|
| CC Bridge | `pm2 logs whatsapp-cc` |
| CC message state | `/home/ubuntu/whatsapp-logger/` |
| CC API | `journalctl -u 1pdb-api` |
| uGridPlan | `journalctl -u ugridplan` on the uGridPlan host |

### Testing Procedure

1. Send a WhatsApp outage report to the CC phone.
2. Confirm a bilingual reply arrives within about 30 seconds.
3. Confirm a tracker-group message is posted.
4. Confirm the ticket appears in the target uGridPlan environment.
5. Confirm downstream notification behavior if that is part of the test scope.

### Troubleshooting

**Bridge not responding**

```bash
pm2 list | grep whatsapp-cc
pm2 logs whatsapp-cc --lines 50 --nostream
pm2 restart whatsapp-cc
```

**Customer lookup failing**

```bash
curl -s -m 5 https://cc.1pwrafrica.com/api/health
curl -s -m 5 http://localhost:8100/health
sudo journalctl -u 1pdb-api --lines 100
```

**uGridPlan ticket creation failing**

- Verify the configured `UGRIDPLAN_API`
- Check bridge logs for auth or timeout errors
- Check uGridPlan service logs on the target host

**OpenClaw refusal loop / non-JSON replies**

If bridge logs show repeated parse failures with outputs like `**NO.**` / `**STOP.**`,
the OpenClaw session context is likely poisoned.

1. SSH to the CC Linux host.
2. Archive the poisoned session file:
   - `~/.openclaw/agents/main/sessions/customer-care.jsonl`
3. Restart the bridge process:
   - `pm2 restart whatsapp-cc`
4. Run a direct OpenClaw smoke test with JSON output to verify healthy responses.

Prevention:
- The bridge now uses a rotating session id (`customer-care-YYYYMMDD`) instead of
  a single long-lived `customer-care` session to reduce long-context poisoning risk.

**WhatsApp re-pairing**

```bash
ssh -i "/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem" ubuntu@<current-cc-linux-host> '
  pm2 stop whatsapp-cc
  rm -rf /home/ubuntu/whatsapp-logger/baileys_auth_cc
  mkdir -p /home/ubuntu/whatsapp-logger/baileys_auth_cc
  pm2 start whatsapp-cc
'
```

## 7. File Reference

### Current Repo Files

| File | Purpose |
|------|---------|
| `whatsapp-bridge/whatsapp-customer-care.js` | WhatsApp bridge runtime |
| `acdb-api/customer_api.py` | CC backend API |
| `acdb-api/requirements.txt` | Backend Python dependencies |
| `acdb-api/frontend/` | Portal frontend |
| `docs/whatsapp-customer-care.md` | This runbook |

### Related Repo Files

| Repo | File area | Purpose |
|------|-----------|---------|
| `uGridPlan` | `web/adapter/om_*` | O&M ticketing and notifications |
| `1PDB` | `services/`, `systemd/`, `migration/` | Canonical ingestion/runtime stack |

## 8. Legacy Note

Historical references to:

- Windows EC2 customer API hosts
- `pyodbc` / Access ODBC
- `.accdb` files as live operational storage
- Task Scheduler / RDP runbooks

should be treated as deprecated unless you are explicitly doing historical
forensics or archival cleanup. The archived ACCDB-era helper scripts live under
`legacy/accdb/`.
