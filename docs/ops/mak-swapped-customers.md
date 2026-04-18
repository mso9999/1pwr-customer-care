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

4. **Customers list vs plot-derived account (fixed in app):** the list **displayed** an account code derived from **plot** (e.g. `0259MAK` from `MAK 0259 HH`) while ThunderCloud / `accounts` had **`0245MAK`**. **Fix:** (a) list links use **`/customers/{postgresql id}`**; (b) **Account** column prefers **`portal_account_number`** from the list API (first `accounts.account_number` for that customer), then plot derivation, then legacy id.

5. **Koios** is **not** the authority for MAK (MAK is ThunderCloud). Comparing CC to Koios for MAK will look like “wrong” names even when TC is consistent.

## Edit policy (ThunderCloud vs CC)

| Phase | Authority | What to do |
|--------|-----------|------------|
| **Until alignment fix is complete** | **ThunderCloud** | Use TC (and meters UI) as the reference for which **customer code** belongs to which **person**. Bring **1PDB** in line with TC via **`fix_mak_drift.py`** (TC → 1PDB). Do not assume CC names/plots are correct until reconciled. |
| **After ops sign off** | **Customer Care / 1PDB** | **Ongoing** name and account maintenance happens in **CC**; changes **push to ThunderCloud** (`sync_thundercloud_customer_name`, registration flows). **Future updates should not** rely on editing TC alone — use CC so 1PDB and TC stay aligned. |

See **`CONTEXT.md`** → *CC → SparkMeter Customer Sync* for the same rule in portal context.

## What to do

| Action | How |
|--------|-----|
| Align CC names to TC | On CC server: `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py` (dry run) then `--apply` with `/opt/1pdb/.env` and TC token |
| Resolve EC2 host | On a machine with AWS creds: see `CONTEXT.md` → `aws ec2 describe-instances` (e.g. `af-south-1`) |

## 2026-04-18 — follow-up: cross-site detail (MAK → SHG)

Even after the list links started using PostgreSQL `id`, the detail page still mis-resolved **one more** endpoint:

- `GET /api/customers/by-id/{n}` queried **`customer_id_legacy`**, not `customers.id`.
- When the URL is `/customers/503` (Mahlompho, MAK), the detail page called `by-id/503`, which returned the row with **`customer_id_legacy = 503`** — a different customer (Maseta, SHG). That customer's `account_numbers` (`0020SHG`) were then shown at the top of Mahlompho's detail page.

**Fix** (`acdb-api/customer_api.py`):

1. New `_resolve_accounts_by_pg_id(cursor, pg_customer_id)` — always joins on `accounts.customer_id` (pg id), immune to legacy/pg collisions.
2. `customer_by_id`: resolves by **`id` first**, falls back to `customer_id_legacy`; returns `pg_customer_id` + `resolved_via`; accounts resolved against the returned row's pg id.
3. `customer_by_account`: same account resolver swapped to pg id.
4. `customer_by_phone`: same.

Net: for any detail URL (account or numeric), accounts now belong to the actual row being displayed.
| DB forensics | SSH to CC host, `psql` — compare `accounts` + `customers` for MAK rows in the workbook |

Cloud agents typically **have no AWS keys** and **no SSH PEM**; run AWS CLI and `psql` on your Mac or a trusted ops host.
