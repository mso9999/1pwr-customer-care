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

## Infrastructure instances (EC2 / af-south-1)

All production servers run in **AWS af-south-1** (Cape Town), account `758201218523`, unless noted otherwise.

| Service | EC2 Name | Instance ID | Public IP | SSH |
|---------|----------|-------------|-----------|-----|
| **CC + 1PDB** (`cc.1pwrafrica.com`) | `EOL` | `i-04291e12e64de36d7` | `13.245.142.186` (Elastic) | `ubuntu@13.245.142.186` key `EOver.pem` port 22 |
| **uGridPlan** (`ugp.1pwrafrica.com`) | `uGridPLAN` | af-south-1 | `15.240.40.213` | `ugridplan@15.240.40.213` port 2222 |
| **Firmware CI runner** (`1meter-build-host`) | staging EC2 | af-south-1 | `13.247.190.132` | `ubuntu@13.247.190.132` port 2222 |

*Resolve the current public IP with:* `aws ec2 describe-instances --region af-south-1 --filters "Name=instance-state-name,Values=running" --query 'Reservations[*].Instances[*].[Tags[?Key==\`Name\`].Value|[0],PublicIpAddress]' --output table`

---

## Cross-cutting

| Topic | Where |
|-------|--------|
| **AWS account** | `758201218523` — use org IAM / SSO, not long-lived keys in repos. |
| **SSH (CC / shared EC2)** | Human Mac: **`/Users/mattmso/Dropbox/AI Projects/secrets/EOver.pem`**. Resolve hostname with **AWS CLI** or GitHub **`EC2_LINUX_HOST`** secret. CI: **`EC2_SSH_KEY`**. Do not rely on old IPs in stray docs. |
| **PostgreSQL (`1PDB`)** | Production `DATABASE_URL` on CC host (`/opt/1pdb/.env` Lesotho, `/opt/1pdb-bn/.env` Benin) and in 1PDB `config/credentials.example.env` for local/dev. |

---

## A — 1PWR Customer Care (`cc.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | Repo → *Settings → Secrets and variables → Actions* | `EC2_SSH_KEY`, `EC2_LINUX_HOST` — see `.github/workflows/deploy.yml`. |
| **Production API env** | `/opt/1pdb/.env` (Lesotho), `/opt/1pdb-bn/.env` (Benin) | `DATABASE_URL`, Koios keys, optional `DATABASE_URL_BN`, bridge URLs, `IOT_INGEST_KEY` (must match Lambda), **`SMS_SERVER_URL`** (outbound customer SMS via gateway PHP), **`SMS_PAYMENT_RECEIPT_ENABLED`** (default on — post-payment balance SMS from `/api/sms/incoming`; set `0` to disable), **`LOW_BALANCE_ALERTS_ENABLED`** for the scheduled low-balance job, etc. Owner `cc_api`. |
| **Portal artifacts** | `/opt/cc-portal/frontend/`, `/opt/cc-portal/backend/` | Deployed from CI; `.env` never rsync'd from git. |
| **Caddy** | `/etc/caddy/Caddyfile`, TLS/ACME | No DB passwords. |
| **WhatsApp bridge** | PM2 on CC host | `CC_BRIDGE_SECRET`, `CC_API`, per-country `CC_BRIDGE_NOTIFY_*` — see `docs/whatsapp-customer-care.md`. |
| **Firebase** | Server-only | `firebase-service-account.json` excluded from deploy rsync; must exist on host if used. |
| **Ingest API** | `acdb-api/ingest.py` | Prototype meter POST `/api/meters/reading` uses env `IOT_INGEST_KEY`; must match **ingestion_gate** Lambda env `ONEPDB_API_KEY`. |
| **SMS gateway ↔ CC balance** | Same env as webhook | **`SMS_GATEWAY_KEY`** authenticates **`GET /api/payments/gateway/balance/{account}`** (balance by account) and **`GET /api/payments/gateway/balances-by-phone?phone=…`** (callback / handset → all accounts + balances). Matches **1PDB** (`balance_engine`), not Koios/ThunderCloud. |

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
| **1PDB balance for outbound SMS** | Team vault + CC `/opt/1pdb/.env` `SMS_GATEWAY_KEY` | PHP should **`GET https://cc.1pwrafrica.com/api/payments/gateway/balance/{account}`** with **`X-Gateway-Key`** — see 1PWR CC `CONTEXT.md` (*SMS payment gateways*). Replaces ThunderCloud/Koios balance reads for customer-facing SMS. **SMS host:** set **`CC_GATEWAY_KEY`** (same value) and optional **`CC_PORTAL_BASE`** in the env used by `sparkmeter/new_file_watcher.php` (see `SMSComms/sparkmeter/cc_1pdb_gateway.php`). |
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
| **AWS IoT** | Per-device certs, IoT policy | Provisioned outside repo; see repo `Docs/` and `aws_documentation/`. MQTT endpoint: `a3p95svnbmzyit-ats.iot.us-east-1.amazonaws.com` (us-east-1). |
| **WiFi / NVS** | On-device NVS flash | Developer provisioning flows; not committed. `sdkconfig.defaults` has the build-time AP SSID/password. |
| **OTA S3 bucket** | `s3://1pwr-ota-firmware/` (us-east-1) | Signed firmware binaries at `firmware-releases/v<X.Y.Z>/<ThingName>/`. Versioning enabled; S3 `VersionId` required when creating AWS IoT OTA jobs. |
| **OTA code signing** | AWS Signer profile **`1PWR_OTA_ESP32_v2`** | ECDSA-P256 signing profile; used in `aws iot create-ota-update` `codeSigning.startSigningJobParameter`. |
| **OTA IAM role** | `arn:aws:iam::758201218523:role/1pwr-ota-service-role` | Passed as `--role-arn` to `aws iot create-ota-update`; grants IoT the right to read S3 + invoke Signer. |
| **CI build runner** | GitHub Actions self-hosted **`1meter-build-host`** | Linux/X64, registered at `onepowerLS/onepwr-aws-mesh` → *Settings → Actions → Runners*. Runs on EC2 `13.247.190.132` port 2222 (ubuntu), af-south-1. Build artifacts at `/opt/1meter-firmware/per-device-builds/`. Runner service: `sudo systemctl restart actions.runner.*`. |
| **GitHub Actions secrets** | Repo → *Settings → Secrets* | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — needed for `publish_to_s3=true` builds. Currently misconfigured on EC2 runner (IAM); use `publish_to_s3=false` + manual S3 upload until fixed. |

---

## H — uGridPlan (`ugp.1pwrafrica.com`, staging `dev.ugp.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | *Secrets* | `EC2_SSH_KEY`, `EC2_HOST` (prod, SSH **port 2222**), `EC2_STAGING_HOST`, `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` (Dropbox import workflow). |
| **Server env** | `/opt/ugridplan/.env`, `/opt/ugridplan/app/.env`, `web/adapter/.env` | Flask/FastAPI secrets, `OM_PORTAL_API_KEY` for O&M portal trust — see `deploy/README.md`. |
| **SSH** | Port `2222` on uGridPlan EC2 | Workflows use `ssh -p 2222`; differs from CC's port 22. |

---

## I — om-portal (`om.1pwrafrica.com`)

| Kind | Location | Notes |
|------|-----------|--------|
| **GitHub Actions** | `EC2_SSH_KEY`, `EC2_LINUX_HOST` | Same CC host as portal deploy; see `.github/workflows/deploy.yml`. |
| **Cross-host** | `UGP_EC2_SSH_KEY` | Used in `server-setup.yml` to configure trust between CC host and uGridPlan host. |
| **Runtime trust** | `OM_PORTAL_API_KEY` | Must match in **Caddy** `/etc/caddy/caddy.env` and **uGridPlan** `/opt/ugridplan/app/web/adapter/.env` (or paths in that repo's deploy docs). |

---

## Related single-repo docs

| Repo | Narrower doc (if present) |
|------|---------------------------|
| 1PWR CC | `docs/credentials-and-secrets.md` — CC-focused quick reference |
| 1PWR CC | `docs/whatsapp-customer-care.md` — bridge env vars and PM2 |
| om-portal | `CADDY_CONFIG.md`, `README.md` — OM API key wiring |
| onepwr-aws-mesh | `Docs/SOP-1meter-ota-setup.md` — step-by-step OTA job creation |
| onepwr-aws-mesh | `Docs/build/CI.md` — CI build workflow guide |

**Maintainers:** When you introduce a new secret class (new country DB, new provider, new GitHub secret name), update **this file in every repo copy** and the index table above.
