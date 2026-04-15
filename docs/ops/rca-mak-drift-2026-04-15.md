# RCA: MAK customer name drift (1PDB vs ThunderCloud)

**Date:** 2026-04-15  
**Session:** ops reconciliation for Ha Makebe (MAK)  
**Canonical remote:** `origin` → `https://github.com/mso9999/1pwr-customer-care.git`

## Scope

- Workbook: `docs/ops/SWAPPED MAK CUSTOMERS.xlsx` (58 data rows) — field observation that **CC list name** and **name when opening the same account** (SparkMeter / ThunderCloud view) diverged for many codes.
- Systems compared: **1PDB** (`customers` + `accounts`) vs **ThunderCloud v0** (`GET /api/v0/customers`, env `TC_API_BASE` / `TC_AUTH_TOKEN` on the CC host).

## RCA classification

| Hypothesis | Verdict |
|------------|---------|
| **Data** — wrong or stale names in 1PDB vs metering system | **Primary.** ThunderCloud is the metering authority for MAK (`CONTEXT.md`). Drift is resolved by aligning 1PDB `first_name` / `last_name` to TC for the same `account_number` / customer `code`. |
| **UI** — portal showing cached or wrong field | **Not supported** for the automated check: CC reads the same DB columns compared to TC; any remaining “wrong screen” would still trace back to DB or to which system is authoritative for display. |
| **Wrong system comparison** — comparing unrelated identifiers | **Rejected** for this workflow: comparison is keyed by **account code** (e.g. `0298MAK`) on both sides. |

Historical context from `scripts/ops/rca_mak_drift.py`: bulk of **0200+** MAK accounts share `created_at = NULL` (legacy migration); newer rows (e.g. 0296–0298MAK) have explicit creation timestamps. Transaction sources for MAK include `thundercloud`, `sms_gateway`, `accdb`, etc. — multiple ingress paths; **customer identity** should still match TC for prepaid operations.

## What we ran (CC production host)

- **AWS:** `i-04291e12e64de36d7` (`af-south-1`), public IP at time of run: `13.245.142.186` (resolve from inventory for future sessions).
- **SSH:** `ssh -i ~/Downloads/EOver.pem ubuntu@<host>` per `CONTEXT.md`.
- Scripts copied to `/tmp/` and executed as `cc_api` with API venv:  
  `/opt/cc-portal/backend/venv/bin/python3 /tmp/rca_mak_drift.py`  
  `/opt/cc-portal/backend/venv/bin/python3 /tmp/fix_mak_drift.py` (dry run, then `--apply`).

### `rca_mak_drift.py` highlights

- Last **code-ordered** name match between 1PDB and TC: **0297MAK**; first mismatch: **0298MAK** (1PDB `Mosala T'siu` vs TC `Mapopela Nhlapho`).
- Displaced-customer spot checks (hard-coded IDs in script) show how legacy persons moved across codes — consistent with **account churn / reassignment**, not a random UI glitch.

### `fix_mak_drift.py` result

- **Before apply:** 1 token-overlap mismatch (strict &lt; 50% name token overlap rule).
- **Applied:** `UPDATE customers SET first_name='Mapopela', last_name='Nhlapho' WHERE id=3011` (account **0298MAK**).
- **After apply:** **0** mismatches by the same heuristic.

**Outstanding (informational, not “name drift” fixes):**

- **TC only:** `0500MAK` — MAK Power House (in TC, not in 1PDB).
- **1PDB only:** 18 accounts (script labels as likely 1Meter / pending / not yet in TC export) — separate onboarding/sync topic, not corrected by name-alignment.

## Workbook vs automated script

The spreadsheet lists **58** apparent swaps. The automated fix uses a **conservative token-overlap** test and only flagged **one** hard mismatch at run time. Rows in the workbook may reflect:

- subtle spelling / ordering that still passes the overlap threshold,
- pairs where **both** CC and “open” views were already updated since the sheet was captured,
- or display paths that are not strictly TC customer name (e.g. meter label vs customer record).

Re-run `fix_mak_drift.py` after any bulk MAK registration or TC imports.

## Note on `docs/ops/agent-handoff-1pwr-cc-repo.md`

That path was **not present** in the repo at the time of this RCA; procedure was inferred from `CONTEXT.md`, the workbook, and `scripts/ops/rca_mak_drift.py` / `fix_mak_drift.py`.

## Deploy / repo follow-up

- Ops scripts under `scripts/ops/` are **not** rsync’d by default to `/opt/cc-portal/backend/`; for repeatability, copy via `scp` or add a documented path on the host.
- Ensure commits that add `docs/ops/SWAPPED MAK CUSTOMERS.xlsx` and this RCA are **pushed** to `mso9999/1pwr-customer-care` (`main`).
