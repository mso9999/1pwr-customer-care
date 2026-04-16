# MAK: CC ↔ ThunderCloud name sync — team cross-check

**Repo:** `mso9999/1pwr-customer-care` · **branch:** `main`

## What shipped in `main`

| Area | Commit / behavior |
|------|-------------------|
| **Full TC vs 1PDB alignment (ops script)** | `acdb-api/scripts/ops/fix_mak_drift.py` (deployed to `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py`) supports **`--sync-all-from-tc`** (dry run) and **`--sync-all-from-tc --apply`** (updates 1PDB wherever normalized full name ≠ TC). Replaces the older token-only heuristic that missed many spreadsheet rows. |
| **CC → ThunderCloud after edits** | Saving **`first_name` / `last_name`** on a customer via **`PUT /api/tables/customers/...`** attempts a ThunderCloud **re-POST** for MAK/LAB; response may include **`thundercloud_sync`** (deploy backend for this). |

See also: `docs/ops/rca-mak-drift-2026-04-15.md`, `docs/ops/SWAPPED MAK CUSTOMERS.xlsx`.

## Cross-check procedure (production host)

1. **Deploy** includes this script with the backend (`git pull` on host or wait for CI). Path on CC: `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py`.
2. **Dry run** (as `cc_api`, API venv — same as other ops scripts):
   ```bash
   sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 /opt/cc-portal/backend/scripts/ops/fix_mak_drift.py --sync-all-from-tc
   ```
4. **Review** each listed diff. **Do not** blindly `--apply` if ThunderCloud shows bad **name** data (e.g. suffix `faulty`, stray `'` in the name). **Fix those in SparkMeter / ThunderCloud first**, then re-run dry run.
5. When the team accepts the TC names as correct:
   ```bash
   sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 /opt/cc-portal/backend/scripts/ops/fix_mak_drift.py --sync-all-from-tc --apply
   ```
6. **Portal check:** spot-check a few accounts in CC vs ThunderCloud UI for the same code.
7. **Ongoing:** edit names in CC; confirm API response **`thundercloud_sync`** success after deploy (or check logs if push fails).

## Accounts only in 1PDB or only in TC

The script prints **TC-only** and **1PDB-only** lists. Name alignment **cannot** fix codes that **do not exist** in ThunderCloud until a TC customer exists (onboarding / meter / SM process).
