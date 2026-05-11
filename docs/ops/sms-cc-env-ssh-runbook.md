# Runbook: SMS ↔ Customer Care environment (SSH)

**Audience:** A human or **local agent with SSH** to the production hosts.  
**Goal:** Configure **outbound** SMS from CC (`SMS_SERVER_URL`) and **inbound** balance/callback calls from the SMS gateway to CC (`CC_GATEWAY_KEY` / `CC_PORTAL_BASE`).

**Security:** Do **not** commit secrets, paste full `.env` files into tickets, or store keys in the git repo. Use the team vault for long-term storage. When rotating keys, update **both** CC and SMS in the same maintenance window.

---

## 1. Concepts (read once)

| Direction | Who initiates | Variable(s) | Purpose |
|-----------|----------------|-------------|---------|
| CC → SMS gateway | FastAPI on CC | **`SMS_SERVER_URL`** | Base URL for `generate_and_send.php` (outbound customer SMS: receipts, contracts, low-balance alerts if wired). |
| SMS gateway → CC | PHP on sms host | **`CC_GATEWAY_KEY`**, optional **`CC_PORTAL_BASE`** | Authenticates `GET /api/payments/gateway/balance/{account}` and `GET /api/payments/gateway/balances-by-phone?phone=…` via header **`X-Gateway-Key`**. |

**Single shared secret:** On CC the canonical name is **`SMS_GATEWAY_KEY`** (`/opt/1pdb/.env`). On the SMS host, PHP reads **`CC_GATEWAY_KEY`** first, then falls back to **`SMS_GATEWAY_KEY`** — see `SMSComms/sparkmeter/cc_1pdb_gateway.php`. **The value must match `SMS_GATEWAY_KEY` on CC.**

**Optional:** **`CC_PORTAL_BASE`** defaults to `https://cc.1pwrafrica.com` in code if unset. Set explicitly when using a non-production hostname.

---

## 2. Prerequisites

- SSH access to:
  - **CC Linux host** (runs `systemd` units `1pdb-api`, optionally `1pdb-api-bn`).
  - **SMS gateway host** (`sms.1pwrafrica.com` — often cPanel; paths may match `SMSComms/deploy.php`: repo under `/home/npower5/repositories/SMSComms`, docroots under `/home/npower5/sms.1pwrafrica.com` and `/home/npower5/sparkmeter.1pwrafrica.com`).
- `sudo` on both hosts for editing protected files and restarting services (adjust if your org uses a config-management user).
- Known-good **`SMS_GATEWAY_KEY`** already present on CC **or** approval to generate a new key and roll it out to both sides.

---

## 3. CC host — `SMS_SERVER_URL` and confirm `SMS_GATEWAY_KEY`

### 3.1 Locate env files

| Service | Typical env file |
|---------|-------------------|
| Lesotho API | `/opt/1pdb/.env` |
| Benin API | `/opt/1pdb-bn/.env` |

Confirm which unit loads which file:

```bash
sudo systemctl cat 1pdb-api | sed -n '/EnvironmentFile/p'
sudo systemctl cat 1pdb-api-bn 2>/dev/null | sed -n '/EnvironmentFile/p'
```

### 3.2 Backup before edit

```bash
sudo cp /opt/1pdb/.env /opt/1pdb/.env.bak.$(date +%Y%m%d%H%M)
# If Benin exists:
sudo cp /opt/1pdb-bn/.env /opt/1pdb-bn/.env.bak.$(date +%Y%m%d%H%M) 2>/dev/null || true
```

### 3.3 Edit Lesotho (`1pdb-api`)

```bash
sudo nano /opt/1pdb/.env
```

Ensure **both** exist (replace URLs if your gateway hostname differs):

```bash
SMS_GATEWAY_KEY=<existing-or-new-shared-secret>
SMS_SERVER_URL=https://sms.1pwrafrica.com/
SMS_PAYMENT_RECEIPT_ENABLED=1
```

Notes:

- **`SMS_SERVER_URL`**: Must reach the **Lesotho** gateway root where `generate_and_send.php` lives (same pattern as contract SMS). Trailing slash is optional; the app normalizes it.
- **`SMS_PAYMENT_RECEIPT_ENABLED`:** Set to `0` to disable post-payment receipt SMS without code changes.

### 3.4 Edit Benin (`1pdb-api-bn`) if that stack sends SMS

Point **`SMS_SERVER_URL`** at the **Benin** gateway base URL (e.g. `https://smsbn.1pwrafrica.com/`), **not** necessarily the Lesotho host.

Ensure **`SMS_GATEWAY_KEY`** in **`/opt/1pdb-bn/.env`** matches whatever the **Benin** SMS PHP host uses for **`CC_GATEWAY_KEY`** (Lesotho and Benin may use **different** keys).

### 3.5 Restart APIs

```bash
sudo systemctl restart 1pdb-api
sudo systemctl restart 1pdb-api-bn 2>/dev/null || true
sudo systemctl status 1pdb-api --no-pager
```

### 3.6 Verify CC health

```bash
curl -fsS http://127.0.0.1:8100/api/health || curl -fsS https://cc.1pwrafrica.com/api/health
# Benin:
curl -fsS http://127.0.0.1:8101/api/health 2>/dev/null || true
```

Check recent logs for outbound SMS warnings:

```bash
sudo journalctl -u 1pdb-api -n 80 --no-pager | grep -i -E 'sms|SMS_SERVER'
```

If **`SMS_SERVER_URL`** is missing, logs may contain: `SMS_SERVER_URL not set — skipping outbound SMS` (from `acdb-api/sms_outbound.py`).

### 3.7 Verify gateway balance endpoint (uses `SMS_GATEWAY_KEY`)

From any machine that can reach CC (replace placeholders):

```bash
export GW_KEY='<value of SMS_GATEWAY_KEY from /opt/1pdb/.env>'
export ACCT='<valid account_number>'
curl -fsS -H "X-Gateway-Key: $GW_KEY" \
  "https://cc.1pwrafrica.com/api/payments/gateway/balance/$ACCT"
```

Expect JSON with **`balance_kwh`**, **`balance_currency`**, **`tariff_rate`**.

---

## 4. SMS host (cPanel / Lesotho gateway) — `CC_GATEWAY_KEY` and optional `CC_PORTAL_BASE`

PHP code lives under repo **`SMSComms`**; deploy copies files into docroots (see `deploy.php`). **`CC_GATEWAY_KEY` is not deployed by git** — set it in the **runtime environment**.

### 4.1 Confirm deployed files

After the latest **`main`** push, check deploy output:

```bash
# Typical paths from deploy.php — adjust user/home if different
tail -20 /home/npower5/sms.1pwrafrica.com/deploy.log
ls -la /home/npower5/sparkmeter.1pwrafrica.com/cc_1pdb_gateway.php
```

### 4.2 Set the secret for **Apache / PHP-FPM** (web requests)

Web PHP (`receive.php`, etc.) reads **`getenv('CC_GATEWAY_KEY')`**.

Typical patterns (pick what matches the server):

1. **Apache `SetEnv`** in the vhost for `sms.1pwrafrica.com` (then `sudo systemctl reload httpd` or `apachectl graceful`).
2. **PHP-FPM pool** for that site, e.g. `env[CC_GATEWAY_KEY] = ...` then **`sudo systemctl reload php-fpm`** (exact PHP version/pool path depends on cPanel).

Also set optional:

```bash
CC_PORTAL_BASE=https://cc.1pwrafrica.com
```

### 4.3 Set the secret for **CLI / cron** (`new_file_watcher.php`)

Cron jobs **do not** inherit Apache environment. If balance or callback handling runs via cron, ensure the cron line **exports** variables before calling PHP, for example:

```cron
*/2 * * * * export CC_GATEWAY_KEY='***same as CC SMS_GATEWAY_KEY***'; export CC_PORTAL_BASE='https://cc.1pwrafrica.com'; /usr/bin/php /home/npower5/sparkmeter.1pwrafrica.com/new_file_watcher.php >> /home/npower5/logs/file_watcher.log 2>&1
```

Prefer a **wrapper script** (`0755`, owned by root or the cron user) that exports vars then execs PHP — avoids putting secrets in `crontab -l` output for casual viewers.

### 4.4 Reload web stack

After Apache/FPM changes:

```bash
sudo systemctl reload httpd 2>/dev/null || sudo systemctl reload apache2 2>/dev/null || true
sudo systemctl reload php-fpm 2>/dev/null || true
```

### 4.5 Smoke test from SMS host (optional)

If `curl` is installed:

```bash
export GW_KEY='<same as CC SMS_GATEWAY_KEY>'
curl -fsS -H "X-Gateway-Key: $GW_KEY" \
  "https://cc.1pwrafrica.com/api/payments/gateway/balance/TEST_ACCOUNT"
```

Use a real **`TEST_ACCOUNT`** known to exist in 1PDB.

---

## 5. End-to-end verification checklist

| Step | Check |
|------|--------|
| CC `.env` | `SMS_SERVER_URL` set; `SMS_GATEWAY_KEY` present. |
| CC restart | `1pdb-api` active; `/api/health` OK. |
| CC gateway API | `curl` balance endpoint with `X-Gateway-Key` returns JSON. |
| SMS `.env`/vhost/cron | `CC_GATEWAY_KEY` matches CC; CLI cron exports if file watcher runs under cron. |
| Deploy | `sparkmeter/cc_1pdb_gateway.php` present on sparkmeter docroot. |
| Behaviour | After an SMS electricity payment, CC logs show outbound SMS attempt (no “SMS_SERVER_URL not set”). Balance/callback SMS text aligns with **1PDB** when gateway key is set (falls back to ThunderCloud if key missing — avoid that in production). |

---

## 6. Rollback

- Restore `.env` backups on CC and **`sudo systemctl restart 1pdb-api`** (and **`1pdb-api-bn`** if edited).
- Remove or revert Apache/FPM/cron env on SMS host and reload services.

---

## 7. Repo references (for the agent)

| Topic | Location |
|-------|-----------|
| Outbound SMS helper | `acdb-api/sms_outbound.py` — **`SMS_SERVER_URL`** |
| Post-payment SMS | `acdb-api/sms_payment_receipt.py` — **`SMS_PAYMENT_RECEIPT_ENABLED`** |
| Gateway balance routes | `acdb-api/payments.py` — **`SMS_GATEWAY_KEY`**, `/gateway/balance`, `/gateway/balances-by-phone` |
| SMS PHP → CC | `SMSComms/sparkmeter/cc_1pdb_gateway.php`, `customer.php`, `new_file_watcher.php` |
| Webhook deploy | `SMSComms/deploy.php` |
| Credential matrix | `docs/inter-repo-credentials.md` |

---

## 8. Agent discipline

1. Never echo secrets into shell history unnecessarily — prefer **`sudo nano`** or short-lived **`export`** in a subshell for testing.
2. Confirm **`refs/heads/main`** deploy succeeded (`deploy.log` on SMS host) before debugging env.
3. If balance SMS still shows ThunderCloud behaviour, suspect **missing `CC_GATEWAY_KEY` on CLI cron** or wrong vhost for Apache.
4. Document the maintenance window and key rotation in the team ops channel, not in git.
