# MAK “swapped customers” spreadsheet

## Canonical location (team)

Human-edited source listing account/name issues for **MAK**:

**`/Users/mattmso/Dropbox/AI Projects/1PWR CC/SWAPPED MAK CUSTOMERS.xlsx`**

This path is on the operator Mac (Dropbox). Cloud CI and repo clones do not have access to it unless you copy the file in.

## Authority and tools

- **MAK** is metered in **ThunderCloud** (SparkMeter), not Koios. Compare CC (`customers` / `accounts`) to ThunderCloud customer `code` → `name`, not to Koios.
- **Automated reconcile (server):** `scripts/ops/fix_mak_drift.py` — report-only or `--apply` to align 1PDB names to ThunderCloud for each `*MAK` account code.
- **RCA script:** `scripts/ops/rca_mak_drift.py` (timeline, boundary, displaced customers).
- **CC UI bug (legacy id vs `customers.id` collision):** fixed by loading customer detail with **`pg_customer_id`** after by-account lookup — see PR / `CustomerDetailPage.tsx`.

## Optional: copy into repo

If you need the spreadsheet in git (e.g. for review or a future import script), copy it next to this doc, for example:

`docs/ops/SWAPPED_MAK_CUSTOMERS.xlsx`

…and add a note in the commit with the export date. Prefer **CSV export** for diff-friendly reviews unless binary Excel is required.
