# Where credentials live (1PWR CC)

**Nothing secret belongs in git.** This file only describes **where** to obtain credentials and runtime config so engineers and AI sessions know what to ask for—**not** the values themselves.

**Org-wide map:** For the same pointers across **all** related repos (1PDB, SMS gateways, uGridPlan, om-portal, Lambda, firmware), see **`docs/inter-repo-credentials.md`** — keep that file in sync in every participating repository.

If your org uses a single vault (e.g. 1Password / Bitwarden / AWS Secrets Manager), add a **one-line pointer** to the canonical vault or runbook here when it exists.

---

## 1. GitHub (this repo)

| Item | Where | Notes |
|------|--------|--------|
| Deploy SSH key | GitHub → **Settings → Secrets and variables → Actions** → `EC2_SSH_KEY` | Private key for `ubuntu@` EC2 deploy user. |
| CC host address | Secret **`EC2_LINUX_HOST`** | Hostname or IP of Linux CC server; do not hardcode in docs (see `README.md`). |

Used by `.github/workflows/deploy.yml` (rsync + systemd restart).

---

## 2. Production EC2 (runtime)

| Item | Where | Notes |
|------|--------|--------|
| Lesotho API + shared services | **`/opt/1pdb/.env`** | `DATABASE_URL` → `onepower_cc`, Koios keys, bridge URLs, optional `DATABASE_URL_BN`, etc. **Owner:** `cc_api`. |
| Benin API | **`/opt/1pdb-bn/.env`** | `DATABASE_URL` → `onepower_bj`, `COUNTRY_CODE=BN`, Koios BN keys. |
| Caddy TLS / routing | **`/etc/caddy/Caddyfile`** | No DB passwords; ACME / site config. |
| WhatsApp bridge | Process env / PM2 | `CC_BRIDGE_SECRET`, `CC_API`, etc. — see `docs/whatsapp-customer-care.md`. |

**How to read:** SSH into the CC host (see `CONTEXT.md` → Manual Access), then `sudo -u cc_api` or read-only as documented in ops runbooks. **Do not** paste `.env` contents into tickets or issues.

**Instance id (AWS):** `CONTEXT.md` → Backup section lists `EOL` (`i-04291e12e64de36d7`, `af-south-1`) for inventory/DLM.

---

## 3. AWS (account + CLI)

| Item | Where | Notes |
|------|--------|--------|
| Account | **758201218523** (from backup bucket naming in `CONTEXT.md`) | Use org‑approved IAM role / SSO; avoid long‑lived root keys in repos. |
| S3 backups | `s3://1pwr-cc-backups-758201218523-af-south-1/...` | Dump artifacts; access via IAM policy, not public. |

There is **no** GitHub‑documented **Secrets Manager** ARN for CC DB password in this repo today—if the org standardizes on one, add it here.

---

## 4. SSH keys (human access)

| Item | Where | Notes |
|------|--------|--------|
| CC EC2 | **`/Users/mattmso/Dropbox/AI Projects/PEMs/EOver.pem`** (canonical on primary dev Mac) | Dropbox-synced PEMs folder; `chmod 600`. Resolve host via AWS or `EC2_LINUX_HOST`. CI uses GitHub secret `EC2_SSH_KEY`. |

---

## 5. Related **other repos** (not this repo)

| System | Repo / host | Credentials |
|--------|-------------|-------------|
| SMS Lesotho | **onepowerLS/SMSComms** — sms.1pwrafrica.com | Hosting / cPanel / FTP as provisioned for that domain; **not** stored in CC repo. |
| SMS Benin | **onepowerLS/SMSComms-BN** — smsbn.1pwrafrica.com | Same; see `docs/ops/bn-sms-1pdb-gap.md`. |
| Android gateway app | **onepowerLS/SMS-Gateway-APP** | Build signing / Play if applicable—separate from CC. |
| Schema & DB migrations | **onepowerLS/1PDB** | Prod DB password may match `DATABASE_URL` on CC host; single source for migrations. |
| uGridPlan | Separate stack | API keys referenced in bridge + CC env as needed. |

**Gap:** cPanel / hosting logins for `sms*.1pwrafrica.com` should be listed in the **infra password manager** or **SMSComms** README—**not** in this repo.

---

## 6. Local development

| Item | Where | Notes |
|------|--------|--------|
| `DATABASE_URL` | Your machine | Copy from a **sanitized** `.env.example` if we add one, or from secure channel—never commit. |
| Default in code | `customer_api.py` etc. | Fallback `postgresql://cc_api@localhost:5432/onepower_cc` is **dev only**; production URLs are on the server. |

---

## 7. What was missing (before this doc)

- **No single map** of GitHub secrets vs server `.env` vs external hosting (SMS PHP).
- **No** pointer to a **team vault** name (if 1PWR uses one org‑wide).

**Maintainers:** When you add a new secret class (e.g. Firebase, new country DB), add a row to §2 or §5 here.
