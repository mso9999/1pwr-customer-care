# WhatsApp Customer Care System

Automated WhatsApp-based customer care for 1PWR Lesotho minigrids. Customers message the CC phone via WhatsApp; an AI classifies the issue, creates an O&M ticket in ugridplan, replies in bilingual Sesotho/English, notifies the operations team via WhatsApp group and email.

Built February 2026. Deployed on staging at `dev.ugp.1pwrafrica.com`.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Infrastructure](#2-infrastructure)
3. [WhatsApp CC Bridge](#3-whatsapp-cc-bridge)
4. [ACDB Customer Lookup API](#4-acdb-customer-lookup-api)
5. [ugridplan O&M Integration](#5-ugridplan-om-integration)
6. [Frontend Changes](#6-frontend-changes)
7. [Operations Guide](#7-operations-guide)
8. [File Reference](#8-file-reference)

---

## 1. System Overview

### Architecture

```
Customer WhatsApp
       |
       v
+------------------+     +------------------+
| CC Bridge        |---->| ACDB API         |
| (Linux EC2)      |     | (Windows EC2)    |
| Node.js/Baileys  |     | FastAPI/pyodbc   |
| PM2 managed      |     | 1,338 customers  |
+------------------+     +------------------+
       |
       +---> OpenClaw AI (classify message, generate bilingual reply)
       |
       +---> ugridplan API (create O&M ticket)
       |         |
       |         +---> Email notification (customercare.LS@1pwrafrica.com)
       |
       +---> WhatsApp group notification ("1PWR LS - OnM Ticket Tracker")
       |
       v
Customer receives bilingual SO/EN reply with ticket number
```

### End-to-End Message Flow

1. Customer sends WhatsApp message to **+266 58342168** (CC phone)
2. CC Bridge receives message via Baileys (multi-device WhatsApp Web API)
3. Bridge filters out own messages, duplicates, old history, protocol messages (7 layers)
4. Bridge looks up customer by phone number via **ACDB API** on Windows EC2
5. **OpenClaw AI** classifies the message: needs ticket? what category/priority? generates bilingual reply
6. If ticket-worthy: Bridge creates ticket via **ugridplan API** (`POST /om/tickets`)
7. ugridplan dispatches **email notification** to customercare.LS@ in background
8. Bridge sends **bilingual reply** (Sesotho first, then English) to customer with ticket number
9. Bridge posts **WhatsApp group notification** to the OnM Ticket Tracker group
10. Follow-up messages within 30 minutes are appended as comments to the existing ticket

---

## 2. Infrastructure

Three EC2 instances in AWS Africa (Cape Town) / af-south-1, all on the same VPC (`172.31.0.0/16`).

### Linux EC2 -- WhatsApp Bridges & OpenClaw

| Field | Value |
|-------|-------|
| **Public IP** | `13.244.104.137` (ai.1pwrafrica.com) |
| **Private IP** | `172.31.3.91` |
| **OS** | Ubuntu |
| **SSH** | `ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137` |
| **PEM key** | `~/Downloads/EOver.pem` |

**Services:**

| Service | Manager | Port | Description |
|---------|---------|------|-------------|
| whatsapp-baileys | PM2 (id 0) | -- | Main WhatsApp logger bridge (Hisense phone) |
| whatsapp-cc | PM2 (id 2) | -- | Customer care bridge (CC phone +266 58342168) |
| OpenClaw | systemd | 18789 | AI gateway for message classification |
| Caddy | systemd | 80/443 | Reverse proxy |

**Key paths on this instance:**

```
/home/ubuntu/whatsapp-logger/
  whatsapp-bridge-baileys.js    # Main logger bridge
  whatsapp-customer-care.js     # CC bridge (this system)
  baileys_auth_cc/              # CC phone WhatsApp credentials
  cc-state.json                 # Persistent state (tracker JID, status)
  cc-conversations.json         # Active conversation tracking
  cc-logs/                      # Message logs (JSONL)
```

### ugridplan EC2 -- O&M Ticketing

| Field | Value |
|-------|-------|
| **Public IP** | `15.240.40.213` |
| **Private IP** | (within VPC) |
| **OS** | Ubuntu |
| **SSH** | Via jump host: `ssh -i /home/ubuntu/.ssh/uGridPLAN.pem -p 2222 ugridplan@15.240.40.213` |
| **PEM key** | `uGridPLAN.pem` (copy to jump host at `/home/ubuntu/.ssh/uGridPLAN.pem`) |
| **Domains** | `ugp.1pwrafrica.com` (prod), `dev.ugp.1pwrafrica.com` (staging) |

**Services:**

| Service | Manager | Port | Description |
|---------|---------|------|-------------|
| ugridplan | systemd | 8017 | FastAPI backend (main.py) |
| nginx | systemd | 80/443 | Reverse proxy + SSL |

**Key paths:**

```
/opt/ugridplan/
  app/web/adapter/main.py            # FastAPI adapter (O&M endpoints)
  app/web/adapter/om_tickets.py      # Ticket CRUD
  app/web/adapter/om_notification_sender.py  # Email dispatch
  data/om_tickets.json               # Ticket data store
  .env                               # Environment config (SMTP, auth, etc.)
  frontend/                          # React build
```

**SSH from Mac (via jump host):**

```bash
# Step 1: Copy PEM to jump host (one-time)
scp -i ~/Downloads/EOver.pem /tmp/uGridPLAN.pem ubuntu@13.244.104.137:/home/ubuntu/.ssh/uGridPLAN.pem
ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137 "chmod 600 /home/ubuntu/.ssh/uGridPLAN.pem"

# Step 2: SSH through jump host
ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137 \
  "ssh -i /home/ubuntu/.ssh/uGridPLAN.pem -p 2222 ugridplan@15.240.40.213 'COMMAND'"
```

### Windows ACDB EC2 -- Customer Database

| Field | Value |
|-------|-------|
| **Private IP** | `172.31.2.39` |
| **Public IP** | (check AWS console) |
| **OS** | Windows Server |
| **Access** | RDP via SSH tunnel |
| **Username** | `Administrator` |
| **Password** | (retrieve via AWS Console -> EC2 -> Get Windows Password) |

**Services:**

| Service | Manager | Port | Description |
|---------|---------|------|-------------|
| ACDBCustomerAPI | Task Scheduler | 8100 | FastAPI customer lookup API |

**Key paths:**

```
C:\acdb-customer-api\
  customer_api.py                # FastAPI service
  requirements.txt               # Dependencies
  venv\                          # Python virtual environment
  logs\                          # stdout/stderr logs

C:\Users\Administrator\Desktop\AccessDB_Clone\
  0112023_1PWRKMETER.accdb       # Access database (1,338 customers)
```

**RDP access from Mac:**

```bash
# Step 1: Create SSH tunnel through Linux EC2
ssh -i ~/Downloads/EOver.pem -L 0.0.0.0:3389:172.31.2.39:3389 -N -f ubuntu@13.244.104.137

# Step 2: Connect via Microsoft Remote Desktop
# PC name: 127.0.0.1
# Username: Administrator
# Password: (from AWS Console)
```

**Network requirements:**
- AWS Security Group: Allow inbound TCP 8100 from `172.31.0.0/16` (VPC CIDR)
- Windows Firewall: Rule "ACDB Customer API" allows inbound TCP 8100

---

## 3. WhatsApp CC Bridge

**Source:** `scripts/whatsapp-customer-care.js` in the Email Overlord repo.
**Deployed to:** `/home/ubuntu/whatsapp-logger/whatsapp-customer-care.js` on Linux EC2.
**Runtime:** Node.js with `@whiskeysockets/baileys` for WhatsApp connectivity.

### Configuration

```javascript
// Phone
CC Phone: +266 58342168

// APIs
UGRIDPLAN_API = "https://dev.ugp.1pwrafrica.com/api"   // staging
ACDB_API      = "http://172.31.2.39:8100"               // Windows EC2 internal

// Auth
UGRIDPLAN_USER = "whatsapp-cc"
// Password: computed from YYYYMM / reverse(YYYYMM), first 4 significant digits

// Paths (on Linux EC2)
AUTH_DIR  = "/home/ubuntu/whatsapp-logger/baileys_auth_cc"
STATE_FILE = "/home/ubuntu/whatsapp-logger/cc-state.json"
CONV_FILE  = "/home/ubuntu/whatsapp-logger/cc-conversations.json"

// Behavior
CONVERSATION_WINDOW = 30 minutes   // follow-ups within window append to ticket
AGENT_TIMEOUT       = 30 seconds   // OpenClaw classification timeout
MSG_DEDUP_MAX       = 500          // message ID dedup cache size
```

### Anti-Spam: 7-Layer Message Filter

Messages pass through these filters before processing:

1. **`msg.key.fromMe`** -- skip own messages (standard Baileys flag)
2. **Status broadcast** -- skip `@broadcast`
3. **Bot number filter** -- skip messages from the bot's own phone number (multi-device echo protection)
4. **Message ID dedup** -- skip already-processed message IDs
5. **Timestamp filter** -- skip messages older than 2 minutes (history sync)
6. **Protocol/system messages** -- skip reactions, key distribution, etc.
7. **Rate limiter** -- max 1 reply per 60 seconds to the same phone number

### AI Classification

Uses OpenClaw agent with a structured prompt. The AI returns JSON:

```json
{
  "needs_ticket": true,
  "category": "no-power",
  "priority": "P1",
  "site_id": "MAK",
  "fault_summary": "Customer reports complete power outage",
  "customer_reply": "Sesotho reply...\n\nEnglish reply...",
  "ask_for_info": false
}
```

**Categories:** no-power, equipment-failure, meter-issue, billing, installation, vegetation, vandalism, complaint, general-inquiry.

**Priority mapping:** P1 (outage), P2 (degraded), P3 (non-critical), P4 (scheduled).

**Language rules:** `customer_reply` is always bilingual -- Sesotho first, blank line, then English. This applies to both AI-generated and hardcoded fallback messages.

### Ticket Creation

The bridge maps AI classification fields to ugridplan ticket fields:

| AI field | Ticket field | Notes |
|----------|-------------|-------|
| site_id | site_id | From customer concession or AI guess; defaults to "LSB" |
| fault_summary | fault_description | 1-sentence technical summary |
| priority | priority | P1-P4 |
| category | equipment_category | Mapped: meter-issue->meter, no-power->electrical, etc. |
| -- | ticket_type | Always "corrective" |
| -- | reported_by | Customer name from ACDB, or "WhatsApp: +phone" |
| -- | customer_id | From ACDB lookup if matched |

### PM2 Resilience

```bash
pm2 start whatsapp-customer-care.js \
  --name "whatsapp-cc" \
  --max-memory-restart 300M \
  --exp-backoff-restart-delay 1000 \
  --max-restarts 50 \
  --kill-timeout 10000

pm2 save          # persist across PM2 restarts
pm2 startup       # persist across EC2 reboots
```

**Application-level resilience:**
- Exponential backoff reconnection (2s -> 4s -> 8s -> ... -> 30s cap)
- Auto re-auth on WhatsApp logout (clears credentials, re-pairs)
- `keepAliveIntervalMs: 25000` heartbeat
- Health monitoring every 5 minutes
- Graceful shutdown preserving state (tracker JID, conversations)

### Concession-to-Site Mapping

The bridge maps ACDB concession names to 3-letter site codes:

```javascript
MAK, Makeneng -> MAK
LEB, Lebakeng -> LEB
MAT, Matsieng -> MAT
SEB, Semonkong -> SEB
// ... (14 sites total, all Lesotho)
```

---

## 4. ACDB Customer Lookup API

**Source:** `scripts/acdb-customer-api/customer_api.py` in the Email Overlord repo.
**Deployed to:** `C:\acdb-customer-api\customer_api.py` on Windows EC2.
**Runtime:** Python 3.14 + FastAPI + pyodbc, connecting to Microsoft Access via ODBC.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check + DB connectivity + customer count |
| GET | `/customers/by-phone/{phone}` | Lookup by phone (searches PHONE, CELL PHONE 1, CELL PHONE 2) |
| GET | `/customers/by-id/{customer_id}` | Lookup by CUSTOMER ID |
| GET | `/customers/by-account/{acct}` | Lookup by account number |
| GET | `/customers/search?q=...&limit=20` | Search by name, village, plot, district |
| GET | `/sites` | List all concessions with customer counts |

### Database

- **File:** `C:\Users\Administrator\Desktop\AccessDB_Clone\0112023_1PWRKMETER.accdb`
- **Size:** ~2.1 GB
- **Customers:** 1,338
- **ODBC Driver:** Microsoft Access Driver (*.mdb, *.accdb) -- 64-bit
- **Key tables:** `tblcustomer`, `tblaccountnumbers`
- **Environment variable:** `ACDB_PATH` (set as machine-level env var)

### Phone Number Normalization

The API normalizes phone numbers for flexible matching:
1. Strip non-digit characters
2. Remove `266` country code prefix (Lesotho)
3. Strip leading zeros
4. Match last 8 digits via SQL `LIKE %XXXXXXXX`

### Customer Response Format

```json
{
  "customer_id": "MAK-0001",
  "first_name": "Thabo",
  "last_name": "Mokhesi",
  "phone": "58001234",
  "cell_phone_1": "",
  "cell_phone_2": "",
  "concession": "MAK",
  "plot_number": "123",
  "account_numbers": ["ACC-001"]
}
```

### Deployment

The API runs as a Windows Scheduled Task configured to start at boot:

```powershell
# Environment variable (set permanently)
[System.Environment]::SetEnvironmentVariable("ACDB_PATH",
  "C:\Users\Administrator\Desktop\AccessDB_Clone\0112023_1PWRKMETER.accdb", "Machine")

# Scheduled Task
Task name:    ACDBCustomerAPI
Action:       C:\acdb-customer-api\venv\Scripts\python.exe customer_api.py
Working dir:  C:\acdb-customer-api
Trigger:      At startup
Run as:       SYSTEM
```

### Interactive API Docs

When running, FastAPI provides auto-generated docs at:
- Swagger UI: `http://172.31.2.39:8100/docs`
- ReDoc: `http://172.31.2.39:8100/redoc`

---

## 5. ugridplan O&M Integration

### Auto-Dispatch Notifications on Ticket Creation

**File:** `web/adapter/main.py`

The `POST /om/tickets` endpoint was modified to automatically dispatch notifications when a ticket is created. Notifications run in a FastAPI `BackgroundTasks` context so the API response is not blocked by SMTP:

```python
@app.post("/om/tickets")
async def create_om_ticket(request: TicketCreate, req: Request = None,
                           background_tasks: BackgroundTasks = None):
    # ... create ticket ...
    
    # Auto-dispatch in background (non-blocking)
    def _dispatch_ticket_notifications(t):
        try:
            dispatch_notification("ticket_created", t)
            if t.get("priority") == "P1":
                dispatch_notification("p1_ticket_created", t)
        except Exception as e:
            logger.warning(f"Notification dispatch failed: {e}")
    
    if background_tasks:
        background_tasks.add_task(_dispatch_ticket_notifications, ticket)
```

### SMTP Configuration

**File:** `web/adapter/om_notification_sender.py`

Fixed to properly handle port 465 (implicit SSL) vs port 587 (STARTTLS):

```python
if cfg["port"] == 465:
    server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
else:
    server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
    if cfg["use_tls"]:
        server.starttls()
```

**Environment variables** (in `/opt/ugridplan/.env` on EC2):

```env
OM_SMTP_HOST=mail.1pwrafrica.com
OM_SMTP_PORT=465
OM_SMTP_USER=mso@1pwrafrica.com
OM_SMTP_PASSWORD=<password>
OM_SMTP_FROM_NAME=1PWR O&M System
OM_SMTP_USE_TLS=true
```

**Sender:** `mso@1pwrafrica.com` (authenticates with mail server)
**Recipient:** `customercare.LS@1pwrafrica.com` (configured as notification contact)

### Notification Contacts and Rules

Created via the ugridplan API:

**Contact:**
- Name: "Customer Care Lesotho"
- Role: dispatcher
- Email: customercare.LS@1pwrafrica.com
- Sites: All 14 Lesotho sites (MAK, LEB, MAT, SEB, TOS, SEH, TLH, MAS, SHG, RIB, KET, RAL, SUA, LSB)

**Rules (4 active):**

| Event | Channels | Conditions |
|-------|----------|------------|
| ticket_created | email | All tickets |
| escalated | email | All escalations |
| p1_ticket_created | email | Priority P1 only |
| status_changed | email | All status changes |

### Date-Based Authentication

The ugridplan API uses `simple` auth mode with a date-based password:

```
Formula: YYYYMM / reverse(YYYYMM)
Example: 202602 / 206202 = 0.9825...
Password: first 4 significant digits = "9825"
```

The CC bridge computes this dynamically via `generateDatePassword()`. The password changes monthly.

---

## 6. Frontend Changes

### PropertyPanel.tsx -- Ticket Creation Button

**File:** `web/frontend/src/components/PropertyPanel.tsx`

Added a "Create Ticket for this [element.type]" button inside the `renderTicketHistory` section. When clicked, opens `OMTicketModal` with pre-filled fields:

- `siteId` -- from `projectId`
- `elementType` -- from `element.type`
- `elementId` -- from `element.id`
- `equipmentCategory` -- mapped from element type (e.g., customer->meter, pole->electrical)
- `customerId` -- from `element.properties.Customer_Code` if available

### OMTicketModal.tsx -- Pre-Fill Props

**File:** `web/frontend/src/components/OMTicketModal.tsx`

Added two new optional props to the interface:

```typescript
interface OMTicketModalProps {
  // ... existing props ...
  equipmentCategory?: string;
  customerId?: string;
}
```

These are used to initialize `formData.equipment_category` and `formData.customer_id` when creating a new ticket from the property panel.

---

## 7. Operations Guide

### Service Management

**CC Bridge (Linux EC2):**

```bash
ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137

pm2 list                           # see all services
pm2 logs whatsapp-cc --lines 30    # recent logs
pm2 restart whatsapp-cc            # restart
pm2 stop whatsapp-cc               # stop
pm2 save                           # persist config
```

**ugridplan (via jump host):**

```bash
ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137 \
  "ssh -i /home/ubuntu/.ssh/uGridPLAN.pem -p 2222 ugridplan@15.240.40.213 \
    'sudo systemctl restart ugridplan'"
```

**ACDB API (Windows EC2 via RDP):**

```powershell
# Check status
Get-ScheduledTask -TaskName "ACDBCustomerAPI"

# Start/Stop
Start-ScheduledTask -TaskName "ACDBCustomerAPI"
Stop-ScheduledTask -TaskName "ACDBCustomerAPI"

# Test
Invoke-RestMethod http://localhost:8100/health
```

### Log Locations

| Component | Location | Command |
|-----------|----------|---------|
| CC Bridge | PM2 logs | `pm2 logs whatsapp-cc --lines 50` |
| CC Messages | `/home/ubuntu/whatsapp-logger/cc-logs/` | JSONL files per day |
| ugridplan | systemd journal | `sudo journalctl -u ugridplan -f` |
| ACDB API | `C:\acdb-customer-api\logs\` | stdout.log, stderr.log |

### Testing Procedure

**End-to-end test:**

1. Send a WhatsApp message from any phone to **+266 58342168**:
   > "I have no electricity at my house in Mazenod since this morning"

2. **Expected within 30 seconds:**
   - Bilingual reply from the CC phone (Sesotho + English)
   - Ticket number in the reply (e.g., `MAK-2026-0065`)

3. **Verify ticket on dev server:**
   - Open `https://dev.ugp.1pwrafrica.com`
   - Log in (employee number + date password)
   - Navigate to O&M dashboard
   - Find the new ticket

4. **Verify WhatsApp group notification:**
   - Check "1PWR LS - OnM Ticket Tracker" group
   - Should show ticket details, customer name, phone, site

5. **Verify email notification:**
   - Check `customercare.LS@1pwrafrica.com` inbox
   - Should receive ticket creation email from "1PWR O&M System"

**Direct API test (no WhatsApp):**

```bash
# Authenticate
TOKEN=$(curl -s -X POST https://dev.ugp.1pwrafrica.com/api/auth/login \
  -H "Content-Type: application/json" \
  -c - \
  -d '{"employeeNumber":"admin","password":"9825"}' | grep access_token | awk '{print $NF}')

# Create ticket
curl -s -X POST https://dev.ugp.1pwrafrica.com/api/om/tickets \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=$TOKEN" \
  -d '{
    "site_id": "GBO",
    "fault_description": "Test ticket from API",
    "priority": "P2",
    "reported_by": "Manual Test",
    "equipment_category": "electrical",
    "ticket_type": "corrective"
  }'
```

### Troubleshooting

**CC bridge not responding to WhatsApp messages:**

```bash
# Check if running
pm2 list | grep whatsapp-cc

# Check for errors
pm2 logs whatsapp-cc --lines 50 --nostream | grep -E "\[FATAL\]|\[ERR\]|\[DISCONNECTED\]"

# Check if connected
pm2 logs whatsapp-cc --lines 20 --nostream | grep "\[CONNECTED\]"

# Force restart
pm2 restart whatsapp-cc
```

**ACDB API timeout (customer lookup fails):**

```bash
# Test from Linux EC2
curl -s -m 5 http://172.31.2.39:8100/health

# If timeout: check Windows EC2
# - Is the API running? (Get-ScheduledTask)
# - Windows Firewall rule present?
# - AWS Security Group allows TCP 8100?
```

**Email notifications not sending:**

```bash
# Check SMTP config on ugridplan EC2
cat /opt/ugridplan/.env | grep OM_SMTP

# Test manual dispatch
curl -s -X POST https://dev.ugp.1pwrafrica.com/api/om/notifications/dispatch \
  -H "Cookie: access_token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event":"ticket_created","ticket_id":"GBO-2026-0001","dry_run":false}'
```

**WhatsApp re-pairing (if CC phone disconnects):**

```bash
# Clear auth and restart (will generate new QR code)
ssh -i ~/Downloads/EOver.pem ubuntu@13.244.104.137 '
  pm2 stop whatsapp-cc
  rm -rf /home/ubuntu/whatsapp-logger/baileys_auth_cc
  mkdir -p /home/ubuntu/whatsapp-logger/baileys_auth_cc
  pm2 start whatsapp-cc
  sleep 10
  pm2 logs whatsapp-cc --lines 20 --nostream | grep -A5 "\[QR\]"
'
# Scan the QR code with the CC phone (+266 58342168)
# In WhatsApp: Settings -> Linked Devices -> Link a Device
```

### Staging vs Production

| Setting | Staging | Production |
|---------|---------|------------|
| ugridplan domain | `dev.ugp.1pwrafrica.com` | `ugp.1pwrafrica.com` |
| CC bridge env var | `UGRIDPLAN_API=https://dev.ugp.1pwrafrica.com/api` | Change to `https://ugp.1pwrafrica.com/api` |
| Ticket data | `/opt/ugridplan/data/om_tickets.json` (staging) | Same path on prod EC2 |

To switch to production, update `UGRIDPLAN_API` in `whatsapp-customer-care.js` and redeploy.

---

## 8. File Reference

### Email Overlord Repo

| File | Purpose |
|------|---------|
| `scripts/whatsapp-customer-care.js` | CC bridge -- WhatsApp message handling, AI classification, ticket creation |
| `scripts/acdb-customer-api/customer_api.py` | ACDB API -- FastAPI service for customer phone/ID/account lookup |
| `scripts/acdb-customer-api/requirements.txt` | Python dependencies: fastapi, uvicorn, pyodbc |
| `scripts/acdb-customer-api/setup.bat` | Windows setup script (venv + pip install) |
| `scripts/acdb-customer-api/install-service.bat` | Windows service installer (NSSM -- unused, Task Scheduler used instead) |

### uGridPlan Repo

| File | Change | Purpose |
|------|--------|---------|
| `web/adapter/main.py` | Modified | Added BackgroundTasks auto-dispatch on `POST /om/tickets` |
| `web/adapter/om_notification_sender.py` | Modified | Fixed SMTP port 465 SSL handling |
| `web/frontend/src/components/PropertyPanel.tsx` | Modified | Added "Create Ticket" button with element pre-fill |
| `web/frontend/src/components/OMTicketModal.tsx` | Modified | Added equipmentCategory and customerId props |

### EC2 Deployed Files (not in repos)

| Instance | Path | Purpose |
|----------|------|---------|
| Linux EC2 | `/home/ubuntu/whatsapp-logger/whatsapp-customer-care.js` | Deployed CC bridge |
| Linux EC2 | `/home/ubuntu/whatsapp-logger/baileys_auth_cc/` | WhatsApp session credentials |
| Linux EC2 | `/home/ubuntu/whatsapp-logger/cc-state.json` | Persistent bridge state |
| Linux EC2 | `/home/ubuntu/whatsapp-logger/cc-conversations.json` | Active conversation tracking |
| Windows EC2 | `C:\acdb-customer-api\customer_api.py` | Deployed ACDB API |
| Windows EC2 | `C:\acdb-customer-api\venv\` | Python virtual environment |
| ugridplan EC2 | `/opt/ugridplan/.env` | SMTP configuration |
| ugridplan EC2 | `/opt/ugridplan/data/om_tickets.json` | Ticket data store |
