# Benin Koios vs CC consumption gap (GBO / SAM)

**Date:** 2026-09-16; live writer confirmed 2026-09-17; last-hour gate 2026-09-17  
**Sites:** Gbowélé (GBO) and Samondji (SAM) only — **not** Lesotho  
**Status:** First ingest fix is on 1PDB `main` / host (`DO UPDATE`, 45-day window, closed day, hour-count completeness). Last-hour energy gate is in 1PDB `services/import_hourly_bn.py` and deploys to `/opt/1pdb/services` on push to 1PDB `main`.  
**Audience:** Ops, finance, and anyone comparing Koios daily reports to Customer Care Analytics.

## Live writer (step 0 — 17 Sep 2026)

SSH to `EOL` (`i-04291e12e64de36d7`). What was on the host **before** the first fix (why the gap existed):

| Item | Value at confirmation |
|------|------------|
| Script | `/opt/1pdb/services/import_hourly_bn.py` (web-session CSV, **not** CC `import_benin_hourly.py`) |
| Timer | `sync_consumption.sh` Phase 3: `import_hourly_bn.py "$WEEK_AGO" --no-aggregate` (to-date defaulted to **today**) |
| Conflict | `ON CONFLICT (meter_id, reading_hour) DO NOTHING` |
| `meter_id` | account (unified 2026-06-09) |
| Skip | `if day_str < MAX(reading_hour)::date` — one row on the latest date retires every earlier day |

That skip is why the 7-day window never repaired Saturday daytime. Pulling **today** plus `DO NOTHING` is why hour 23 stayed at ~⅓ of Koios.

**After first deploy (same day):** 45 days → yesterday UTC, `DO UPDATE`, skip only when ≥23 hours and ≥17 daytime hours. First 45-day tick upserted 17,283 rows; 18 site-days stayed incomplete because Koios’s current file is still thin. Hour-count skip still treated 24-slot days with stub hour 23 as done (e.g. GBO 2026-09-05, h23/h21 = 0.215).

**Last-hour gate (this change):** a site-day is also incomplete when hour 23 is missing or site-total hour-23 kWh is < ⅓ of hour 21. The timer then re-pulls and `DO UPDATE`s until Koios’s later file fills the stub. `--repair` is bounded to the CLI date range (default: yesterday) so a year of historical stubs is not scraped by accident. Do not run pre-45-day `--repair` until new closed days prove the gate.

---

## Problem

A side-by-side of **deduped Koios daily-report kWh** and **CC Analytics hourly download** shows CC about **5–10% lower** than Koios on monthly totals for GBO + SAM (Aug 2025 – Aug 2026).

This is **not** a customer-register mismatch and **not** leftover heartbeat duplicates. After those were removed, two separate gaps remain:

1. **Missing hours** — Koios has a `(account, hour)` and CC has no row. Worse **during the day (hours 0–18)** and on **weekends**.
2. **Thin last hours** — both sides have the hour, but CC kWh is short. Almost all of this sits in **UTC hours 19–23**, especially **hour 23 (~−65% vs Koios)**.

Missing hours explain why dropping “CC empty / Koios present” cut the error from about **−10% to −5%** in some months. Thin hour 23 explains most of the leftover **−5%** in 2025-H2 and early 2026.

---

## How the comparison was done (so it can be repeated)

| Step | What was used |
|---|---|
| Koios | Daily report 15-minute `kilowatt_hours`, **deduped** on `(meter serial, heartbeat_start)` before binning to the hour |
| CC | Analytics **hourly download** (`GET /api/analytics/consumption-export`), not the Total Consumption metric |
| Grain | One row per **(account, hour)** on both sides |
| Universe | Only **accounts present in both** files |

Ruled out before concluding ingest bugs:

- Raw `SUM` of duplicated Koios CSV rows (importers already collapse those; Jan 2026 BN was up to ~7× / 49×).
- Analytics **Total Consumption** metric (that reads stale `monthly_consumption`, not hourly).
- Non-client / unmapped Koios meters (site loads, empty customer codes).

The hourly download is still **not** “every Koios meter we stored.” It inner-joins `accounts` + `customers` and keeps **one** row per `(account, hour)` (`DISTINCT ON`, `meter_id DESC`). For this study that grain was matched on the Koios side.

---

## Findings

### Monthly error (`CC − Koios`)

| Month | All hours | Hours 0–18 | 0–18 overlap only |
|---|---:|---:|---:|
| 2025-08 | −5.34% | +0.36% | +0.34% |
| 2025-09 | −6.50% | +0.44% | +0.53% |
| 2025-10 | −8.01% | −2.79% | −2.58% |
| 2025-11 | −6.86% | −1.52% | −1.54% |
| 2025-12 | −6.18% | −1.31% | −1.06% |
| 2026-01 | −5.41% | −0.69% | −0.58% |
| 2026-02 | −5.86% | −1.18% | −1.01% |
| 2026-03 | −5.74% | −0.97% | −0.75% |
| 2026-04 | −6.38% | −0.57% | −0.59% |
| 2026-05 | −6.29% | −2.35% | −3.55% |
| 2026-06 | −9.41% | −5.03% | **−0.06%** |
| 2026-07 | −7.47% | −7.16% | −4.56% |
| 2026-08 | −5.91% | −3.90% | −3.17% |

**How to read the columns**

- **All hours** — every hour in the month (the original −5% to −9%).
- **Hours 0–18** — drop UTC 19–23 (throws away the thin last-hour problem).
- **0–18 overlap only** — daytime hours that exist on **both** sides (throws away missing CC hours).

**Three regimes**

| Period | What the −5% to −9% is |
|---|---|
| 2025-08 – 2025-09 | Almost only hours 19–23. Daytime overlap ≈ 0 (CC slightly high). |
| 2025-10 – 2026-04 | Mostly 19–23, plus ~1% daytime. |
| 2026-05 | Coverage both ways; overlap worse than 0–18 (CC has extra hours Koios does not). |
| 2026-06 | **Missing daytime hours** (0–18 −5%) + evening. Overlap **−0.06%** = when a row exists, kWh matches. |
| 2026-07 – 2026-08 | Daytime dominates (0–18 ≈ all-hours). Both holes and short overlapping hours. |

June 2026 overlap ≈ 0 is the control: the two files **can** agree when CC stored a complete report day.

### Hour-of-day error (energy-weighted `CC − Koios`)

| UTC hour | Pattern |
|---|---|
| 0–4 | Steady ~−3% |
| 5–16 | Near zero / slightly positive (hour 6 a small + spike) |
| 17–18 | Large **mean** (+26% / +48%), **total** ~0 — a few outliers, ignore for the month |
| 19–22 | Increasingly negative (−5% → −13%) |
| **23** | **−63% mean / −66% total** — CC keeps about **one-third** of Koios |

Hour 23 is 1/24 of the day. A −66% miss there is ~**2.7% of monthly kWh**. Hours 19–22 add another ~1.5–2%. Together they are the leftover −5% after missing hours are excluded.

Dropping hour 23 (then 19–23) from a month in the 2025-08–2026-04 band should move All-hours next to the 0–18 column.

---

## Causes

Benin does **not** use Lesotho’s Koios `data/historical` importer for this series. GBO/SAM are written by the **daily report** path. Do not “fix” `acdb-api/import_hourly.py` for this table.

### Live BN path (as documented)

`sync_consumption.sh` Phase 3, every ~15 minutes:

| Piece | Script (host) | Method |
|---|---|---|
| Hourly consumption | `/opt/1pdb/services/import_hourly_bn.py` | Koios **web-session** daily-report CSV |
| Window | `$WEEK_AGO` | ~7-day rolling re-fetch |
| Conflict | Historically `ON CONFLICT DO NOTHING` | First write wins for that `(meter_id, hour)` |

Repo copies that can also write BN:

- `scripts/ops/import_benin_hourly.py` — `GET /api/v2/report`, `DO UPDATE`, `meter_id = account` (sum of serials).
- `scripts/ops/import_koios_report.py` — same report API, `meter_id = serial`, `DO UPDATE` of `kwh` + `community` but **not** `account_number`.

`meter_id` has been **account** and **serial** at different times. The Analytics download then `DISTINCT ON (account, hour)` and keeps `meter_id DESC`. Two conventions in one month can drop a meter’s kWh from the CSV even when it is in `hourly_consumption`.

Benin is **UTC+1 (WAT)**. Importers parse `heartbeat_start` as a **naive** clock (first 19 characters, no offset). The hourly download labels timestamps **UTC**. A local-vs-UTC mix will pile error into clock-hour 23. Verify on one SAM file whether `heartbeat_start` is WAT or UTC before changing bins.

### Cause A — Missing daytime / weekend hours

`DO NOTHING` still **inserts** a new hour. A hole means that hour was never in a successful pull after Koios had it, and the job then **stopped asking**.

Nova meters do not always flush 15-minute heartbeats in real time. Daytime load often sits on the meter until evening backhaul. On Saturday/Sunday the site is quieter; the gateway may sit until Monday. The Koios file for that date is **incomplete at first**, then fills in.

Typical chain:

1. First pull of “yesterday” (often 00:00–06:00 UTC) writes the hours Koios already has — overnight, not 08:00–18:00.
2. Later in the week Koios adds daytime / weekend heartbeats.
3. CC never stores them because:
   - a “this day already has rows → skip” / freshness check (any night hour counts as done), or
   - the **7-day** window has expired (Saturday daytime appearing Monday + Koios lag is day 8–9), or
   - the **web-session** scrape 401s/404s over the weekend (cookie idle) and the date is treated as “no file.”

That is why 0–18 is worse than 19–23 for missing hours, and why weekends are over-represented. It is the same class of miss as the 2026-04 BN gap (Phase 3 used `$YESTERDAY` only; fixed to `$WEEK_AGO`) — the window is still too short for weekend + late upload.

### Cause B — Thin hours 19–23 (especially 23)

Both sides have the hour; CC has ~⅓ of the energy at hour 23.

On this path the likely mechanism is **ingesting an open local day**, not LS’s `date_range from=to=D` on `data/historical`:

1. The timer pulls **today** while the WAT day is still open. Hour 23 (and 19–22) are missing from the file or only the first interval is present.
2. That stub is inserted.
3. **`DO NOTHING`** refuses yesterday’s complete report for those hours.

Hours 07:00–16:00 were already in the file on first sight, so 2025-08/09 daytime overlap ≈ 0.

If Phase 3 is already `import_benin_hourly.py` with `DO UPDATE`, thin hour 23 is instead a **timezone / report-day** issue (step “Confirm the live writer” below). First-write-of-today + `DO NOTHING` is what CONTEXT still describes on the host.

### What this is not

- The June 2026 **Lesotho** `hourly_consumption` partition / `id` default outage (does not explain GBO/SAM).
- Koios interval **duplicates** (already removed on both sides; feed guard threshold is raw/dedup > 1.05).
- Dashboard / O&M “consumption” that is actually **`transactions.kwh_value` (vended kWh)**.

---

## Solution

No code in this PR. Implement in this order on **Benin** only.

### 0. Confirm the live writer (gate)

On the CC host, record what Phase 3 actually runs:

- script (`import_hourly_bn.py` vs `import_benin_hourly.py`)
- conflict clause (`DO NOTHING` vs `DO UPDATE`)
- `meter_id` (account vs serial)

Until that is known, do not change LS `import_hourly.py`.

### 1. Missing hours (daytime / weekends) — the gap this note is mainly about

A day is **not done** until it is complete. One night-time row must not retire the date.

1. **Completeness gate** after each SAM/GBO date:
   - distinct hours ≈ 24 (or 23 if hour 23 is still treated separately), **and**
   - 0–18 hour-count is not far below 19–23, **and**
   - meter/account count is in line with the previous complete weekday.
2. Keep incomplete dates on a **retry list**. Re-pull them for **30–60 days**, not 7. Upsert (insert missing hours; overwriting thin hours is fine).
3. **Monday morning** force-pull **Thu–Sun** for GBO and SAM (weekend + Friday lag).
4. Use **`GET /api/v2/report` + API key** as the writer. Web-session CSV is a fallback only — keys do not die over the weekend.
5. **404 = not ready**, not “zero consumption.” Leave the date on the retry list.
6. **Alarm:** any GBO/SAM date in the last 14 days with 0–18 coverage ≪ 19–23, or Sat/Sun hour-count ≪ the prior weekday.

**One-time repair:** list days where CC account-hours ≪ the Koios report (weekends first). Re-download each date and insert missing `(meter_id, reading_hour)` rows. Do not skip a date because some hours exist. Success: 0–18 “all hours” moves next to 0–18 overlap (June already proves overlap can be ~0).

### 2. Thin hour 23 (and 19–22)

Depends on step 0:

- If the live job still uses **`DO NOTHING`** and pulls **today**: only ingest **closed WAT days**, or keep today but **`DO UPDATE`** `kwh`, `account_number`, and `community` so tonight’s stub is replaced tomorrow.
- If it already **upserts**: parse `heartbeat_start` with an explicit timezone (`Africa/Porto-Novo` vs UTC — verify on one file) and store `timestamptz` UTC so the hourly download and Koios use the same clock.

After each site-day, the **last local hour** should have ~4 intervals per live meter. If hour 23 kWh is ~⅓ of hour 21, fail the day and retry. Implemented in `import_hourly_bn.py` as `last_hour_is_thin` (site-total kWh, default ratio ⅓, override `BN_LAST_HOUR_MIN_RATIO`). Hour-count-complete days with a stub last hour stay on the retry list inside the 45-day window.

### 3. One `meter_id` convention

Pick **serial** (one kWh per meter) **or** **account** (sum serials, `meter_id = account`). Do not leave both. Then a one-time BN dedup so the Analytics download cannot hide a meter via `DISTINCT ON`.

Optional later: an “all meters” export flag so the next comparison does not have to re-learn the grain.

### 4. Keep `monthly_consumption` in step on `onepower_bj`

BN has no reliable rebuild on the live importer. Analytics **metrics** (Total Consumption) read that table. Rebuild daily with `MAX(kwh)` per `(account, hour)` then `SUM` by month (same as LS). The hourly download does not need this; staff using the metric do.

### 5. Prove it on **new** GBO/SAM days

After the timer uses a completeness gate + overwrite + closed days (or upsert):

- All-hours ≈ 0–18 overlap (near 0, like 2025-08 0–18).
- Hour 23 Total_Err% loses the −65% spike.
- Weekend 0–18 holes stop appearing after the Monday catch-up.

Use June 2026 0–18 overlap ≈ 0 as the control.

---

## Suggested implementation order

1. Identify the live BN script and conflict / `meter_id` behaviour.  
2. Completeness gate + no skip-if-any-rows (stops new daytime / weekend holes). **Done** (hour count).  
3. 30–60 day retry + Monday Thu–Sun pull. **45-day window done**; Monday force-pull still optional.  
4. API report as the writer.  
5. Closed-day only or `DO UPDATE` (hour 23). **Done.** Last-hour energy retry **on 1PDB `main`**.  
6. Explicit WAT→UTC.  
7. Single `meter_id` convention.  
8. BN `monthly_consumption` rebuild.  
9. Backfill incomplete historical days. **Do not `--repair` before 45 days until new days prove out.**  
10. Alarms (missing 0–18 / weekend; thin last hour).

Steps 2–4 are the missing-data fix. Step 5 is the thin hour-23 fix. They are independent; do the missing-data work first if weekend holes are the operational pain.

---

## Related code and docs

| Item | Role |
|---|---|
| `scripts/ops/import_benin_hourly.py` | API daily report → hourly, account-keyed, `DO UPDATE` |
| `scripts/ops/import_koios_report.py` | Same report API, serial-keyed; `account_number` not updated on conflict |
| `acdb-api/analytics.py` `GET /consumption-export` | Hourly download used in this study |
| Host `import_hourly_bn.py` + `sync_consumption.sh` Phase 3 | Live BN writer (1PDB repo / `/opt/1pdb/services/`) |
| `docs/ops/upstream-recon-2026-05-02-BN.md` | Earlier GBO 2025-10 miss (day 8/16/24 sample) |
| `CONTEXT.md` § BN Data Pipeline | Timer and web-CSV description (may lag the host) |

---

## Protocol note

Lesotho has a **different** writer (`data/historical`, `from = to = D`, skip degraded daily lumps, 3-day report healer). That path can also thin hour 23 and drop days, but **this measurement is GBO + SAM only.** Do not apply an LS-only patch and call this closed.
