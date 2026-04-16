# Agent handoff: work in the **1PWR CC** repository (local org clone)

## Context: two workspaces

- **Cursor cloud / CI workspace** may clone **`mso9999/1pwr-customer-care`** from GitHub (`/workspace`). That clone did **not** contain `docs/ops/SWAPPED MAK CUSTOMERS.xlsx` on `main` when checked (user pushes may target a **different remote** or folder).
- **Your assignment** assumes you are opened in the **1PWR CC** project folder on the operator Mac, e.g.  
  **`/Users/mattmso/Dropbox/AI Projects/1PWR CC`**  
  (exact path may differ; confirm with `git remote -v` and repo root.)

**First step:** From the project root, run:

```bash
pwd
git remote -v
git branch --show-current
git status
ls -la docs/ops/
```

Confirm **`SWAPPED MAK CUSTOMERS.xlsx`** exists under `docs/ops/` and whether `origin` points at **`github.com/mso9999/1pwr-customer-care`** or another URL. If the workbook is only local, commit and push so GitHub and other agents can see it.

---

## Business problem

Operations reports **names in Customer Care (CC / 1PDB) do not match names in Koios**, and **opening a customer in CC shows a different name** than expected. For **MAK** (Maseru mini-grid), the metering authority is **ThunderCloud (SparkMeter)**, not Koios — comparing CC to Koios for MAK will mislead.

---

## Technical background (from `1pwr-customer-care` codebase)

1. **Customer detail by account** (`/customers/NNNNMAK`): After `GET /api/customers/by-account/{acct}`, the UI must load the full row using PostgreSQL **`customers.id`** (`pg_customer_id` in API), **not** only `customer_id_legacy`. Otherwise, if `customer_id_legacy` equals **another row’s `id`**, CRUD returns the **wrong person** (identity collision). Fix lives in `customer_api.py` (`pg_customer_id`) and `CustomerDetailPage.tsx` — ensure your branch has merged this if the bug still reproduces.

2. **MAK name drift vs ThunderCloud:** `scripts/ops/rca_mak_drift.py` (RCA) and `acdb-api/scripts/ops/fix_mak_drift.py` (align 1PDB names to TC; deployed to `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py`) — run **on the CC Linux host** with `/opt/1pdb/.env` and TC credentials. MAK is **ThunderCloud**; use TC as truth for `code` → `name`.

3. **Docs:** `docs/ops/mak-swapped-customers.md`, `docs/ops/apply-cc-migrations.md`, `CONTEXT.md` (SSH, AWS host resolution).

4. **Reader script (when xlsx is in repo):** `scripts/ops/read_swapped_mak_xlsx.py` — dumps the spreadsheet; requires `pip install openpyxl`.

---

## Your tasks (in order)

### 1. Reconcile repo and spreadsheet

- [ ] Confirm git root and remotes; align with **`mso9999/1pwr-customer-care`** if org standard is a single GitHub repo.
- [ ] Ensure **`docs/ops/SWAPPED MAK CUSTOMERS.xlsx`** is committed and pushed to the remote you use for production deploys.
- [ ] Run `python3 scripts/ops/read_swapped_mak_xlsx.py` (or open the xlsx) and **list columns and sample rows** (account codes, names CC vs field/Koios/TC).

### 2. Root-cause analysis (RCA)

- [ ] For each row in the workbook, classify:
  - **A)** 1PDB name wrong vs **ThunderCloud** (same account code) → data fix: `fix_mak_drift.py` or targeted SQL updates.
  - **B)** UI showing wrong customer despite correct DB → **pg_customer_id** / routing bug.
  - **C)** Comparing CC to **Koios** for MAK → explain mismatch is expected; re-validate against **TC**.
- [ ] If scripts reference specific legacy IDs (e.g. `rca_mak_drift.py` “displaced” list), verify whether those still match production DB.

### 3. AWS / server verification (on a host with credentials)

**Not available in keyless cloud agents** — run on Mac or bastion:

```bash
aws sts get-caller-identity
aws ec2 describe-instances --region af-south-1 \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId,Tags[?Key==`Name`].Value|[0],PublicDnsName,PublicIpAddress]' \
  --output table
```

SSH to CC host (see `CONTEXT.md`: `EOver.pem`, `ubuntu@<host>`).

### 4. Database checks (on CC server, `psql` + `DATABASE_URL`)

- For accounts listed in the spreadsheet: join **`accounts`** → **`customers`** on `customer_id`; compare `first_name`/`last_name` to ThunderCloud API for same `account_number`.
- Check for **duplicate or ambiguous** `customer_id_legacy` / account reassignment history if `mutations` or audit tables exist.

### 5. Deliverables

- Short **RCA write-up**: what caused each class of mismatch, evidence (query results or API snippets).
- **Remediation plan**: SQL or `fix_mak_drift.py --apply`, UI deploy for `pg_customer_id` if needed, communication to ops (Koios vs TC for MAK).
- Optional: **CSV export** of the workbook committed next to the xlsx for diffs and agent readability.

---

## Constraints

- **1PDB** is source of truth for CC **after** corrections; **ThunderCloud** is truth for **MAK** meter/customer **codes** and names when reconciling field vs portal.
- Do **not** paste secrets, `.env` contents, or PEM material into tickets; use env on server only.
- Prefer **failing fast** and documented fixes over silent fallbacks (project engineering principles).

---

## Quick reference commands (CC server)

```bash
# Dry run name alignment MAK vs TC
sudo -u cc_api bash -c 'cd /opt/cc-portal/backend && python3 scripts/ops/fix_mak_drift.py'

# Apply (after review)
# python3 scripts/ops/fix_mak_drift.py --apply
```

(Adjust paths if backend lives elsewhere; scripts may need to be copied from repo to `/opt/cc-portal/backend/scripts/ops/` or run from a git clone on the server.)

---

## Session log

After completing work, append a summary to **`SESSION_LOG.md`** per `.cursorrules`.
