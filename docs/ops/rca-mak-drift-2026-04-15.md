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

### A. Decide authority (policy, not only tech)

| If the business decides… | Then… |
|--------------------------|--------|
| **ThunderCloud name is authoritative for field / metering** | Keep **aligning 1PDB → TC** for display and care workflows (`fix_mak_drift.py` direction). CRM edits in CC should trigger a **manual** TC update (or future API if SparkMeter adds one). |
| **1PDB / CC is authoritative for legal / CRM name** | **Manually update ThunderCloud** to match CC (same API gap — usually **SparkMeter UI** or vendor process). Do **not** only change 1PDB and assume TC will follow. |

Until TC exposes name updates via API, **both** sides require **explicit** coordination when names change.

### B. Immediate (clean existing drift)

1. Re-run **`scripts/ops/fix_mak_drift.py`** on the CC host (venv as `cc_api`) after any bulk registration or known TC import — **dry run first**, then `--apply` if policy is TC-wins.
2. For the **58 workbook rows**: for each code, compare **current** 1PDB vs **current** TC API (`GET /api/v0/customers`). Where the script’s heuristic passes but staff still see a problem, **manually** reconcile (spreadsheet as checklist).
3. Track **accounts in 1PDB not returned in the TC bulk customer list** and **TC-only org meters** — separate onboarding; not fixed by renaming alone.

### C. Process (prevent new drift)

1. **Name change SOP for MAK/LAB:** when ops edits a customer name in CC, add a step **“Update ThunderCloud / SparkMeter to match”** (or open a ticket) until an API exists.
2. **Meter assignment:** if `sm_sync` returns `already_exists`, have staff **verify** TC name matches CC (API lookup vs 1PDB) before closing the task.
3. **After ACCDB / bulk imports:** run reconciliation before go-live for ThunderCloud sites.

### D. Engineering (optional hardening)

1. **Scheduled report:** cron on CC host or CI: compare TC vs 1PDB for MAK/LAB weekly; email or Slack on **any** mismatch (even soft).
2. **On customer update (MAK/LAB):** after `PUT` on `customers`, **GET** TC customer by code; if names differ, return a **warning** in the API response (non-blocking) so the UI can show “meter system may still show a different name.”
3. If SparkMeter ever documents a **customer update** endpoint for ThunderCloud, implement a **`push_customer_name_to_tc()`** and call it from the customer update path — until then, document the limitation prominently in ops runbooks.

### E. What not to do

- Do not assume **only** fixing 1PDB fixes what technicians see on **ThunderCloud**-backed UIs — **TC may stay stale** until manually updated or an API exists.
- Do not relax the token matcher in `fix_mak_drift.py` blindly for auto-apply; use **report-only** fuzzy output first to avoid overwriting correct CRM names with bad TC data.

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
