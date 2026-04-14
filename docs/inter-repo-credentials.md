# Inter-repo credential map (1PWR)

**Nothing secret belongs in git.** This file only lists **where** to obtain credentials and runtime config (GitHub Actions secrets, server paths, env var names, team vault). **Values** live in the password manager, on servers, or in GitHub Secrets—not in repositories.

**Canonical copies:** The same document should exist as `docs/inter-repo-credentials.md` in each participating repo so anyone working from any checkout can find cross-system locations. When you change it, update **every** copy (or merge one PR that touches all repos).

**Team vault:** If the org uses a single vault (1Password, Bitwarden, AWS Secrets Manager naming scheme), add a one-line pointer in this section.

| Index | Repo | GitHub |
|-------|------|--------|
| A | **1PWR Customer Care** (portal) | [mso9999/1pwr-customer-care](https://github.com/mso9999/1pwr-customer-care) |
| B | **1PDB** (schema, services, migrations) | [onepowerLS/1PDB](https://github.com/onepowerLS/1PDB) |
| C | **SMSComms** (Lesotho SMS PHP) | [onepowerLS/SMSComms](https://github.com/onepowerLS/SMSComms) |
| D | **SMSComms-BN** (Benin SMS PHP) | [onepowerLS/SMSComms-BN](https://github.com/onepowerLS/SMSComms-BN) *(same doc should be added there)* |
| E | **SMS-Gateway-APP** (Android gateway) | [onepowerLS/SMS-Gateway-APP](https://github.com/onepowerLS/SMS-Gateway-APP) |
| F | **ingestion_gate** (prototype meter Lambda) | [onepowerLS/ingestion_gate](https://github.com/onepowerLS/ingestion_gate) |
| G | **onepwr-aws-mesh** (ESP32 firmware) | [onepowerLS/onepwr-aws-mesh](https://github.com/onepowerLS/onepwr-aws-mesh) |
| H | **uGridPlan** (O&M backend, planning) | [onepowerLS/uGridPlan](https://github.com/onepowerLS/uGridPlan) |
| I | **om-portal** (O&M SPA on CC host) | [onepowerLS/om-portal](https://github.com/onepowerLS/om-portal) |

**SMS stack:** Production uses **two** gateway server deployments — **Lesotho** (`sms.1pwrafrica.com`, repo **SMSComms**) and **Benin** (`smsbn.1pwrafrica.com`, **SMSComms-BN** or `smsbn/` tree). The **SMS-Gateway-APP** Android build is one codebase; each site configures its own gateway URL. CC (`/api/sms/incoming` vs `/api/bn/sms/incoming`) is the mirror target — see `CONTEXT.md` → *SMS payment gateways*.

---

## Cross-cutting

| Topic | Where |
|-------|--------|
| **AWS account (CC backups, etc.)** | `758201218523` referenced from backup bucket naming in 1PWR CC `CONTEXT.md` — use org IAM / SSO, not long-lived keys in repos. |
| **SSH (CC / shared EC2)** | Human Mac: canonical PEM folder **`/Users/mattmso/Dropbox/AI Projects/PEMs`** (e.g. `EOver.pem` for CC). CI: `EC2_SSH_KEY` secret. Hostnames/IPs from AWS inventory or `EC2_LINUX_HOST` — do not rely on old IPs in stray docs. |
| **PostgreSQL (`1PDB`)** | Production `DATABASE_URL` values are on the CC host (`/opt/1pdb/.env`, `/opt/1pdb-bn/.env`) and in 1PDB’s `config/credentials.example.env` template for local/dev. |

---

## A — 1PWR Customer Care (`cc.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | Repo → *Settings → Secrets and variables → Actions* | `EC2_SSH_KEY`, `EC2_LINUX_HOST` — see `.github/workflows/deploy.yml`. |
| **Production API env** | `/opt/1pdb/.env` (Lesotho), `/opt/1pdb-bn/.env` (Benin) | `DATABASE_URL`, Koios keys, optional `DATABASE_URL_BN`, bridge URLs, `IOT_INGEST_KEY` (must match Lambda), etc. Owner `cc_api`. |
| **Portal artifacts** | `/opt/cc-portal/frontend/`, `/opt/cc-portal/backend/` | Deployed from CI; `.env` never rsync’d from git. |
| **Caddy** | `/etc/caddy/Caddyfile`, TLS/ACME | No DB passwords. |
| **WhatsApp bridge** | PM2 on CC host | `CC_BRIDGE_SECRET`, `CC_API`, per-country `CC_BRIDGE_NOTIFY_*` — see `docs/whatsapp-customer-care.md`. |
| **Firebase** | Server-only | `firebase-service-account.json` excluded from deploy rsync; must exist on host if used. |
| **Ingest API** | `acdb-api/ingest.py` | Prototype meter POST `/api/meters/reading` uses env `IOT_INGEST_KEY`; must match **ingestion_gate** Lambda env `ONEPDB_API_KEY`. |

---

## B — 1PDB

| Kind | Location | Notes |
|------|-----------|--------|
| **Local template** | `config/credentials.example.env` → copy to `.env` | `DATABASE_URL`, AWS keys, IoT cert paths, `KOIOS_*`, `THUNDERCLOUD_*`, `SMS_GATEWAY_URL`. |
| **Production** | Overlaps CC host `/opt/1pdb/.env` | Migrations and services expect same DB roles as CC API. |
| **SMS outbound** | `SMS_GATEWAY_URL` | Points at Lesotho gateway (e.g. `sms.1pwrafrica.com` PHP) — gateway-side credentials are not in this repo. |

---

## C — SMSComms (Lesotho, `sms.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **PHP / DB** | `db.php` on the gateway host | DB address and credentials for SMS metadata; not in git. |
| **SMS provider** | `send.php` / CM.com | API keys for outbound SMS — hosting or env on PHP server. |
| **Hosting** | cPanel / FTP / provider | Domain and TLS managed outside CC repo. |

---

## D — SMSComms-BN (Benin, `smsbn.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **Pattern** | Same as SMSComms | DB + provider keys on Benin gateway host. |
| **CC integration** | CC API `/api/bn/sms/incoming` | PHP must POST to CC; see 1PWR CC `docs/ops/bn-sms-1pdb-gap.md`. |

---

## E — SMS-Gateway-APP (Android)

| Kind | Location | Notes |
|------|-----------|--------|
| **Signing / Play** | Developer machines + Google Play Console | Release keys and service accounts are not in git. |

---

## F — ingestion_gate (AWS Lambda)

| Kind | Location | Notes |
|------|-----------|--------|
| **Lambda env** | AWS Console → Lambda → configuration | `ONEPDB_READING_URL` (default targets CC `/api/meters/reading`), `ONEPDB_API_KEY` — **must match** CC `IOT_INGEST_KEY`. |
| **AWS** | Execution role | DynamoDB (`1meter_data`, `meter_last_seen`), S3 (`1meterdatacopy`), per `meter_ingest_gate.py`. |

---

## G — onepwr-aws-mesh (firmware)

| Kind | Location | Notes |
|------|-----------|--------|
| **AWS IoT** | Per-device certs, IoT policy | Provisioned outside repo; see repo `Docs/` and `aws_documentation/`. |
| **WiFi / NVS** | On-device | Developer provisioning flows; not committed. |

---

## H — uGridPlan (`ugp.1pwrafrica.com`, staging `dev.ugp.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | *Secrets* | `EC2_SSH_KEY`, `EC2_HOST` (prod, SSH **port 2222**), `EC2_STAGING_HOST`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` (Dropbox import workflow). |
| **Server env** | `/opt/ugridplan/.env`, `/opt/ugridplan/app/.env`, `web/adapter/.env` | Flask/FastAPI secrets, `OM_PORTAL_API_KEY` for O&M portal trust — see `deploy/README.md`. |
| **SSH** | Port `2222` on uGridPlan EC2 | Workflows use `ssh -p 2222`; differs from CC’s port 22. |

---

## I — om-portal (`om.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | `EC2_SSH_KEY`, `EC2_LINUX_HOST` | Same CC host as portal deploy; see `.github/workflows/deploy.yml`. |
| **Cross-host** | `UGP_EC2_SSH_KEY` | Used in `server-setup.yml` to configure trust between CC host and uGridPlan host. |
| **Runtime trust** | `OM_PORTAL_API_KEY` | Must match in **Caddy** `/etc/caddy/caddy.env` and **uGridPlan** `/opt/ugridplan/app/web/adapter/.env` (or paths in that repo’s deploy docs). |

---

## Related single-repo docs

| Repo | Narrower doc (if present) |
|------|---------------------------|
| 1PWR CC | `docs/credentials-and-secrets.md` — CC-focused quick reference |
| 1PWR CC | `docs/whatsapp-customer-care.md` — bridge env vars and PM2 |
| om-portal | `CADDY_CONFIG.md`, `README.md` — OM API key wiring |

**Maintainers:** When you introduce a new secret class (new country DB, new provider, new GitHub secret name), update **this file in every repo copy** and the index table above.
