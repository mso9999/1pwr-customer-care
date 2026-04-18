# 1Meter fleet connectivity monitor

Alerts the WhatsApp Customer Care tracker group when prototype meters stop reporting to AWS IoT (via DynamoDB `meter_last_seen`) past a threshold, and again when they recover.

## What it does

- Scans **`meter_last_seen`** in `us-east-1` every **15 minutes** (systemd timer).
- For any meter whose **`lastAcceptedTime`** is older than **`THRESHOLD_HOURS`** (default **6**), posts a WhatsApp message to the bridge on CC (same `/notify` channel used by SMS phone-fallback alerts).
- Cross-references the CC **`meters`** table to label rows with `account_number` and `role` (e.g. customer **check** vs **gateway**/**repeater**), so the alert distinguishes customer issues from infra.
- De-duplicates via `/var/lib/cc-fleet-monitor/state.json` — new offline meters alert once; recovered meters produce a one-shot recovery message.

## Files

| Path | Purpose |
|------|---------|
| `scripts/ops/monitor_1meter_offline.py` | Monitor entry point (stdlib + `boto3` + `psycopg2`). |
| `deploy/systemd/cc-1meter-monitor.service` | oneshot service; loads `/opt/1pdb/.env` and optional `/etc/default/cc-1meter-monitor`. |
| `deploy/systemd/cc-1meter-monitor.timer` | Every 15 min, `Persistent=true`. |

## Install on CC host

```bash
sudo install -o cc_api -g cc_api -m 755 \
  /opt/cc-portal/backend/scripts/ops/monitor_1meter_offline.py \
  /opt/cc-portal/backend/scripts/ops/monitor_1meter_offline.py

sudo cp deploy/systemd/cc-1meter-monitor.service /etc/systemd/system/
sudo cp deploy/systemd/cc-1meter-monitor.timer   /etc/systemd/system/

sudo install -d -o cc_api -g cc_api /var/lib/cc-fleet-monitor

sudo systemctl daemon-reload
sudo systemctl enable --now cc-1meter-monitor.timer
sudo systemctl list-timers cc-1meter-monitor.timer --no-pager

# Optional: override threshold per host
# echo 'THRESHOLD_HOURS=6' | sudo tee /etc/default/cc-1meter-monitor
```

## Env

| Var | Default | Notes |
|-----|---------|-------|
| `THRESHOLD_HOURS` | `6` | Alert when a meter has been silent this long. |
| `AWS_REGION` | `us-east-1` | DynamoDB region for `meter_last_seen`. |
| `DDB_TABLE` | `meter_last_seen` | Table name. |
| `DATABASE_URL` | `postgresql://cc_api@localhost:5432/onepower_cc` | 1PDB read for role labels. |
| `CC_BRIDGE_NOTIFY_URL` | — | From `/opt/1pdb/.env`. |
| `CC_BRIDGE_SECRET` | — | From `/opt/1pdb/.env`. |
| `STATE_FILE` | `/var/lib/cc-fleet-monitor/state.json` | Owner `cc_api`. |
| `FLEET_SITE` | `MAK` | Label in the alert text. |
| `DRY_RUN` | `0` | `1` to log only. |

## Alert format

```
⚠️ 1Meter fleet alert (MAK): 10/10 offline (>6h)
• 23022673 (0045MAK, check) — last: 29.0h ago
• 23022667 (gateway) — last: 28.9h ago
• …

✅ 1Meter recovered (MAK):
• 23022673 (0045MAK, check) back online
• …
```

## Notes

- `role`/`account_number` come from 1PDB — IDs not present in `meters` (e.g. unassigned bench units) just show the short serial.
- WA inbound channel is the same pattern as `cc_bridge_notify`; if those env vars are absent, the monitor logs only and exits 0.
- Threshold of **6 h** aligns with Check Meters `status = offline` cutoff in `om_report`.

## Enabling WhatsApp alerts on the host

CC hosts do not currently set `CC_BRIDGE_NOTIFY_URL` / `CC_BRIDGE_SECRET`, so the monitor (and the existing SMS phone-fallback notifier in `ingest.py`) logs only. To switch alerts on:

1. Find the WA bridge inbound port (defaults to a local port in `whatsapp-customer-care.js`; set by `BRIDGE_INBOUND_PORT` in PM2 env if customized) and the shared secret used by the bridge (`CC_BRIDGE_SECRET` in the bridge process).
2. Add to `/opt/1pdb/.env` (owned by `cc_api`):
   ```bash
   CC_BRIDGE_NOTIFY_URL=http://127.0.0.1:<bridge-inbound-port>/notify
   CC_BRIDGE_SECRET=<shared-secret>
   ```
3. `sudo systemctl restart 1pdb-api 1pdb-api-bn cc-1meter-monitor.timer` (the last is optional — the timer reloads env per run).
4. Verify: `sudo systemctl start cc-1meter-monitor.service --wait; sudo journalctl -u cc-1meter-monitor.service -n 20 --no-pager`.
