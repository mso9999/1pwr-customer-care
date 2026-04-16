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

---

## Deep RCA: why “swapped” or mismatched customer details happen

“Swapped” in the workbook means: **same account code** shows **one name in CC (1PDB)** and **another when opening the customer in the SparkMeter / ThunderCloud context** (meter UI, technician workflow, or API-backed name). That is not a rendering bug in the portal; it is **two stores disagreeing on identity for the same code**.

### 1. No automated name sync after initial creation (structural)

The CC → SparkMeter integration is **create-only** for customer identity:

```11:14:acdb-api/sparkmeter_customer.py
Constraints:
  - Koios can create customers without a meter assignment.
  - ThunderCloud requires a physical meter serial at creation time.
  - Neither platform supports customer name updates via API.
```

So:

- **Registration** or **first successful TC create** sends **one** name snapshot to ThunderCloud.
- Later edits to **`customers.first_name` / `last_name` in CC** (generic CRUD) **do not** push to ThunderCloud — there is nothing in the API layer today to do so, and the upstream APIs are documented as not supporting name updates.
- **Meter assign** only calls `create_sparkmeter_customer` when **no** TC customer exists yet; if TC already has the code, CC reports `already_exists` and **does not refresh** the name from 1PDB:

```396:414:acdb-api/meter_lifecycle.py
            existing = lookup_sparkmeter_customer(account_number)
            if not existing:
                full_name = " ".join(
                    filter(None, [customer.get("first_name"), customer.get("last_name")])
                )
                sm_r = create_sparkmeter_customer(
                    account_number=account_number,
                    name=full_name,
                    meter_serial=meter_id,
                )
                sm_sync = {"success": sm_r.success, "platform": sm_r.platform}
                if sm_r.sm_customer_id:
                    sm_sync["sm_customer_id"] = sm_r.sm_customer_id
                if sm_r.error and not sm_r.success:
                    sm_sync["error"] = sm_r.error
                if sm_r.skipped:
                    sm_sync["skipped"] = True
            else:
                sm_sync = {"success": True, "platform": "existing", "already_exists": True}
```

Any name correction in CC after TC already had a record therefore **leaves TC stale** unless someone updates ThunderCloud manually.

### 2. Legacy migration and account churn

Many MAK rows predate clean `created_at` timestamps (`NULL` from ACCDB migration). **Account numbers can be reassigned** or **customer rows can move** while meters and TC still reflect older assignments — `rca_mak_drift.py`’s “displaced customer” checks illustrate **same person / family name appearing under different codes** in TC vs where 1PDB expects them. That reads as “swapped” to field staff even when each system is internally consistent with a different story.

### 3. ThunderCloud or on-site edits

If names were changed **only** in SparkMeter / ThunderCloud (or legacy on-prem tools), **1PDB would not mirror** those edits automatically.

### 4. Why the workbook (58 rows) vs `fix_mak_drift.py` (1 strict mismatch)

`fix_mak_drift.py` only flags pairs where **token overlap** between 1PDB and TC full names is below a **50%** heuristic. That misses:

- **Near-duplicates** (same family, small spelling differences) that still look “wrong” to humans.
- Cases where **both** systems were fixed after the spreadsheet was captured.
- Rows where the “opened” name comes from **another field** (e.g. meter label vs customer name) — worth confirming in the field for a few codes.

So: **operational truth** = treat the workbook as a **human audit list**; **automated truth** = script + optional fuzzy/manual review.

---

## How to solve it (recommended program)

### A. Authority (policy)

**Canonical source of truth:** **CC / 1PDB** for customer identity.

**This incident:** ThunderCloud had the **correct** names and CC did not — so the remediation is a **one-time** sync **TC → 1PDB** (`fix_mak_drift.py` or manual `UPDATE` from TC).

**After that back-sync:** Ongoing changes are edited **in CC**; the API **pushes names to ThunderCloud** on `PUT /api/tables/customers/{id}` when `first_name` / `last_name` are updated (`sync_thundercloud_customer_name` → re-POST `POST /api/v0/customer/`). Response may include **`thundercloud_sync`** per MAK/LAB account; failures are logged and do not roll back 1PDB.

### B. Immediate (clean existing drift)

1. **One-time:** **`acdb-api/scripts/ops/fix_mak_drift.py`** on the CC host (`/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py`, venv as `cc_api`) — **dry run**, then **`--apply`** to align 1PDB to verified-good TC names.
2. For the **58 workbook rows**: for each code, compare **current** 1PDB vs **current** TC API (`GET /api/v0/customers`). Where the script’s heuristic passes but staff still see a problem, **manually** reconcile (spreadsheet as checklist).
3. Track **accounts in 1PDB not returned in the TC bulk customer list** and **TC-only org meters** — separate onboarding; not fixed by renaming alone.

### C. Process (prevent new drift)

1. **Name changes for MAK/LAB:** edit **in CC**; confirm **`thundercloud_sync`** in the update response or logs. If push fails, use SparkMeter UI or vendor — do not leave 1PDB and TC divergent on purpose.
2. **Meter assignment:** if `sm_sync` returns `already_exists`, have staff **verify** TC name matches CC (API lookup vs 1PDB) before closing the task.
3. **After ACCDB / bulk imports:** run reconciliation before go-live for ThunderCloud sites.

### D. Engineering (optional hardening)

1. **Scheduled report:** cron on CC host or CI: compare TC vs 1PDB for MAK/LAB weekly; alert on drift (catches failed re-POSTs or manual TC edits).
2. **UI:** surface **`thundercloud_sync`** failures from the customer update response so staff retry or use SparkMeter UI.
3. If SparkMeter documents a dedicated **PATCH** customer endpoint, prefer it over re-POST when available.

### E. What not to do

- Do not treat **one-time TC→1PDB** as “ThunderCloud is always authoritative” — CC remains canonical; that sync only fixed **this** drift episode.
- Do not relax the token matcher in `fix_mak_drift.py` blindly for auto-apply; use **report-only** fuzzy output first if TC data quality is uncertain for a code.

## What we ran (CC production host)

- **AWS:** `i-04291e12e64de36d7` (`af-south-1`), public IP at time of run: `13.245.142.186` (resolve from inventory for future sessions).
- **SSH:** `ssh -i ~/Downloads/EOver.pem ubuntu@<host>` per `CONTEXT.md`.
- Scripts copied to `/tmp/` and executed as `cc_api` with API venv:  
  `/opt/cc-portal/backend/venv/bin/python3 /tmp/rca_mak_drift.py`  
  `sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 /opt/cc-portal/backend/scripts/ops/fix_mak_drift.py` (dry run, then `--apply`).

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

## Follow-up: full-string TC → 1PDB sync (2026-04-15)

- **Policy:** ThunderCloud was treated as **authoritative for this one-off**; ongoing name edits should be made **in CC**, which **re-POSTs** to ThunderCloud for MAK/LAB via `sync_thundercloud_customer_name` on customer update (`acdb-api/crud.py`).
- **Script:** `/opt/cc-portal/backend/scripts/ops/fix_mak_drift.py --sync-all-from-tc --apply` on the CC host (venv as `cc_api`, env from `/opt/1pdb/.env`). Source in repo: `acdb-api/scripts/ops/fix_mak_drift.py` (deployed with backend).
- **TC name cleanup:** Leading quotes and trailing ` faulty` are stripped from TC display names before compare/apply (SparkMeter glitches).
- **Result:** **9** `customers` rows updated so full name matches sanitized TC for accounts present **in both** TC export and 1PDB. Post-run `--sync-all-from-tc` dry run: **0** mismatches on that intersection.
- **Not fixed by renaming:** **`0500MAK`** — in TC only (MAK Power House). **47** accounts in **1PDB only** vs TC bulk list (new registrations / not yet in ThunderCloud export). **Workbook rows** in SWAPPED MAK CUSTOMERS.xlsx that fall in that bucket are **not** a CC↔TC name mismatch until TC lists the same account code.

## Note on `docs/ops/agent-handoff-1pwr-cc-repo.md`

That path was **not present** in the repo at the time of this RCA; procedure was inferred from `CONTEXT.md`, the workbook, and `scripts/ops/rca_mak_drift.py` / `fix_mak_drift.py`.

## Deploy / repo follow-up

- Ops scripts under `scripts/ops/` are **not** rsync’d by default to `/opt/cc-portal/backend/`; for repeatability, copy via `scp` or add a documented path on the host.
- Ensure commits that add `docs/ops/SWAPPED MAK CUSTOMERS.xlsx` and this RCA are **pushed** to `mso9999/1pwr-customer-care` (`main`).
