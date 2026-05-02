# 1PDB coverage audit — BN (2026-05-02T05:45:45.440487+00:00)

Window: last **8** months. Stale threshold: **30** days. Deficit threshold: **50%** of trailing median.

**Database:** `localhost:5432/onepower_bj`

## 1. Per-site coverage overview

| Site | Active meters | Zero-coverage meters | Zero % | Stale meters (>30d) |
| --- | --- | --- | --- | --- |
| GBO | 103 | 3 | 2.9 | 2 |
| SAM | 66 | 1 | 1.5 | 2 |
| TEST | 10 | 10 | 100.0 | 0 |

## 2. Per-month coverage matrix

| Site | 2025-09 | 2025-10 | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GBO | 33,567 / 53m | 51 / 51m | 36,572 / 65m | 67,045 / 99m | 64,153 / 96m | 57,434 / 180m | 39,117 / 91m | 84,202 / 201m | 2,471 / 103m |
| SAM | 52,525 / 100m | 20,257 / 104m | 57,782 / 107m | 61,791 / 110m | 66,936 / 111m | 51,159 / 109m | 22,321 / 55m | 48,426 / 118m | 1,398 / 62m |

_Cell format: rows / distinct meters._

## 3. Monthly deficits (rows < 50% of baseline)

In-progress months (i.e. the current month) are compared against a **prorated** baseline so day-2-of-31 isn't reported at 97% missing.

**Complete months below threshold:**

| Site | Month | Rows | Baseline median | Missing % |
| --- | --- | --- | --- | --- |
| GBO | 2025-10 | 51 | 57,434 | 99.9% |
| SAM | 2025-10 | 20,257 | 52,525 | 61.4% |
| SAM | 2026-03 | 22,321 | 52,525 | 57.5% |

**In-progress month (prorated):**

| Site | Month | Rows | Expected so far | Missing % |
| --- | --- | --- | --- | --- |
| SAM | 2026-05 | 1,398 | 3,345 | 58.2% |


## 4. Last ingest per (site, source)

| Site | Source | Last reading | Last insert | Rows total |
| --- | --- | --- | --- | --- |
| GBO | koios | 2026-05-01 | 2026-05-02 | 416,414 |
| SAM | import | 2026-02-28 | 2026-04-02 | 361,354 |
| SAM | koios | 2026-05-01 | 2026-05-02 | 292,794 |

## 5. Zero-coverage meters

Total: **14** active meters with no `hourly_consumption` rows for their `account_number`.

First 25 (sorted by site, then account):
| Site | Account | Meter ID | Role | Connect date |
| --- | --- | --- | --- | --- |
| GBO | 000033GBO | SMRSD-04-0002DF08 | primary | -- |
| GBO | 0131GLBO | SMRSD-04-0009A426 | primary | -- |
| GBO | 0189GBO | SMRSD-04-0009A408 | primary | -- |
| SAM | 0054SAM | SMRSD-04-0008FD10 | primary | -- |
| TEST | test 3 | SMRSD-04-0002EBDF | primary | -- |
| TEST | test 4 | SMRSD-04-0004F905 | primary | -- |
| TEST | test 6 | SMRSD-04-00036BAE | primary | -- |
| TEST | test 7 | SMRSD-04-000347E3 | primary | -- |
| TEST | test 8 | SMRSD-04-000367A1 | primary | -- |
| TEST | test 9 | SMRSD-04-00034DE6 | primary | -- |
| TEST | test1 | SMRSD-04-0002FE96 | primary | -- |
| TEST | test10 | SMRSD-04-00035154 | primary | -- |
| TEST | test11 | SMRSD-04-0004CEFD | primary | -- |
| TEST | test2 | SMRSD-04-00021B9E | primary | -- |


## 6. Stale meters (>30 days since last reading)

Total: **4**.

| Site | Account | Meter ID | Last reading | Days stale |
| --- | --- | --- | --- | --- |
| GBO | 0159GBO | SMRSD-04-000356BA | 2025-09-30 | 213 |
| GBO | 0171GBO | SMRSD-04-0002931E | 2025-12-11 | 141 |
| SAM | 0014SAM | SMRSD-04-0004FA31 | 2026-03-04 | 58 |
| SAM | 0029SAM | SMRSD-04-0004E3DE | 2026-03-05 | 58 |


## 7. Cross-country meters (wrong DB)

_No cross-country leak detected in this DB._

## 8. Sites declared in `country_config` but absent from `hourly_consumption`

_All declared sites have at least some data._

## 9. Sites in data but not in `country_config` (orphans)

_All sites with data are declared in country_config._
