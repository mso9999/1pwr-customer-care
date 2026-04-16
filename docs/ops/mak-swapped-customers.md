# MAK “swapped customers” workbook

## File location

Place **`SWAPPED MAK CUSTOMERS.xlsx`** in **`docs/ops/`** and **push to GitHub** so agents and CI can read it:

```bash
cd "/path/to/1pwr-customer-care"
git add "docs/ops/SWAPPED MAK CUSTOMERS.xlsx"
git commit -m "docs(ops): add SWAPPED MAK CUSTOMERS workbook"
git push origin main
```

Verify on GitHub: **Repository → docs/ops** should list the `.xlsx`.

**Read locally / in workspace:** `python3 scripts/ops/read_swapped_mak_xlsx.py` (requires `pip install openpyxl`).

## RCA (how mixups happen)

1. **ThunderCloud is source of truth for MAK** — account `code` (e.g. `0218MAK`) maps to a **name** in SparkMeter. Customer Care (`1PDB`) stores names on **`customers`** and links **`accounts.account_number`** → **`customers.id`**.

2. **Drift** = same account code in CC shows **different** `first_name` / `last_name` than TC. Documented investigation: `scripts/ops/rca_mak_drift.py` (creation timeline, first mismatch boundary, “displaced” customers still in DB under other accounts).

3. **UI bug (fixed in app):** opening `/customers/NNNNMAK` previously used `customer_id_legacy` for CRUD `getRecord`; if that number **collided** with another row’s PostgreSQL `id`, the **wrong** person appeared. **Fix:** API `GET /api/customers/by-account/...` includes **`pg_customer_id`** (real primary key); `CustomerDetailPage` uses it for `getRecord`.

4. **Koios** is **not** the authority for MAK (MAK is ThunderCloud). Comparing CC to Koios for MAK will look like “wrong” names even when TC is consistent.

## What to do

| Action | How |
|--------|-----|
| Align CC names to TC | On CC server: `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py` (dry run) then `--apply` with `/opt/1pdb/.env` and TC token |
| Resolve EC2 host | On a machine with AWS creds: see `CONTEXT.md` → `aws ec2 describe-instances` (e.g. `af-south-1`) |
| DB forensics | SSH to CC host, `psql` — compare `accounts` + `customers` for MAK rows in the workbook |

Cloud agents typically **have no AWS keys** and **no SSH PEM**; run AWS CLI and `psql` on your Mac or a trusted ops host.
