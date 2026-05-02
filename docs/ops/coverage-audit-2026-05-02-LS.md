# 1PDB coverage audit — LS (2026-05-02T05:45:42.581402+00:00)

Window: last **8** months. Stale threshold: **30** days. Deficit threshold: **50%** of trailing median.

**Database:** `localhost:5432/onepower_cc`

## 1. Per-site coverage overview

| Site | Active meters | Zero-coverage meters | Zero % | Stale meters (>30d) |
| --- | --- | --- | --- | --- |
| BVM | 1 | 1 | 100.0 | 0 |
| GBO | 135 | 41 | 30.4 | 94 |
| HHQ | 1 | 1 | 100.0 | 0 |
| KET | 172 | 17 | 9.9 | 24 |
| LSB | 29 | 5 | 17.2 | 0 |
| MAK | 278 | 17 | 6.1 | 7 |
| MAS | 203 | 21 | 10.3 | 51 |
| MAT | 284 | 22 | 7.7 | 55 |
| RIP | 3 | 1 | 33.3 | 0 |
| SAM | 67 | 9 | 13.4 | 58 |
| SEH | 10 | 3 | 30.0 | 0 |
| SHG | 368 | 58 | 15.8 | 68 |
| TLH | 100 | 24 | 24.0 | 3 |
| UNK | 4 | 1 | 25.0 | 3 |

## 2. Per-month coverage matrix

| Site | 2025-09 | 2025-10 | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GBO | -- | -- | -- | -- | -- | 96 / 96m | -- | -- | -- |
| KET | 58,177 / 95m | 31,932 / 123m | 76,645 / 133m | 88,832 / 156m | 4,525 / 141m | 21,644 / 147m | 2,351 / 111m | 30,182 / 135m | -- |
| LSB | 5,746 / 25m | 9,101 / 25m | 15,120 / 25m | 16,109 / 25m | 4,772 / 25m | 2,324 / 25m | 19 / 19m | 2,315 / 24m | -- |
| MAK | 144,210 / 204m | 145,694 / 204m | 144,492 / 205m | 149,115 / 206m | 154,330 / 224m | 155,290 / 237m | 298,631 / 489m | 219,242 / 490m | 6,636 / 254m |
| MAS | 68,951 / 119m | 39,382 / 126m | 57,899 / 163m | 3,618 / 184m | 3,835 / 184m | 20,527 / 183m | 28,037 / 132m | 36,487 / 133m | 836 / 111m |
| MAT | 148,055 / 240m | 86,610 / 245m | 134,964 / 244m | 174,437 / 259m | 38,513 / 257m | 20,987 / 257m | 4,876 / 189m | 40,864 / 210m | -- |
| SAM | -- | -- | -- | -- | -- | 117 / 58m | -- | -- | -- |
| SEH | 1,607 / 4m | 1,037 / 7m | 4,073 / 7m | 4,543 / 7m | 343 / 7m | 741 / 7m | 222 / 8m | 566 / 8m | 8 / 8m |
| SHG | 114,658 / 248m | 80,462 / 275m | 108,528 / 273m | 131,153 / 312m | 44,363 / 307m | 39,188 / 308m | 58,138 / 235m | 75,210 / 246m | 1,762 / 205m |
| TLH | 41,921 / 68m | 21,374 / 69m | 42,651 / 68m | 35,811 / 76m | 12,724 / 76m | 5,657 / 75m | 11,019 / 70m | 20,119 / 74m | -- |

_Cell format: rows / distinct meters._

## 3. Monthly deficits (rows < 50% of baseline)

In-progress months (i.e. the current month) are compared against a **prorated** baseline so day-2-of-31 isn't reported at 97% missing.

**Complete months below threshold:**

| Site | Month | Rows | Baseline median | Missing % |
| --- | --- | --- | --- | --- |
| LSB | 2026-03 | 19 | 5,746 | 99.7% |
| MAT | 2026-03 | 4,876 | 86,610 | 94.4% |
| KET | 2026-03 | 2,351 | 31,932 | 92.6% |
| MAS | 2025-12 | 3,618 | 36,487 | 90.1% |
| MAS | 2026-01 | 3,835 | 36,487 | 89.5% |
| KET | 2026-01 | 4,525 | 31,932 | 85.8% |
| SEH | 2026-03 | 222 | 1,037 | 78.6% |
| MAT | 2026-02 | 20,987 | 86,610 | 75.8% |
| TLH | 2026-02 | 5,657 | 21,374 | 73.5% |
| SEH | 2026-01 | 343 | 1,037 | 66.9% |
| LSB | 2026-04 | 2,315 | 5,746 | 59.7% |
| LSB | 2026-02 | 2,324 | 5,746 | 59.6% |
| MAT | 2026-01 | 38,513 | 86,610 | 55.5% |
| MAT | 2026-04 | 40,864 | 86,610 | 52.8% |
| SHG | 2026-02 | 39,188 | 80,462 | 51.3% |

**In-progress month (prorated):**

| Site | Month | Rows | Expected so far | Missing % |
| --- | --- | --- | --- | --- |
| SEH | 2026-05 | 8 | 57 | 86.1% |
| SHG | 2026-05 | 1,762 | 5,022 | 64.9% |
| MAS | 2026-05 | 836 | 2,081 | 59.8% |


## 4. Last ingest per (site, source)

| Site | Source | Last reading | Last insert | Rows total |
| --- | --- | --- | --- | --- |
| GBO | koios | 2026-02-18 | 2026-02-19 | 96 |
| KET | koios | 2026-04-30 | 2026-05-01 | 598,478 |
| LSB | koios | 2026-04-30 | 2026-05-01 | 95,961 |
| MAK | iot | 2026-05-02 | 2026-05-02 | 2,511 |
| MAK | koios | 2025-07-26 | 2026-03-21 | 5,878 |
| MAK | thundercloud | 2026-05-02 | 2026-05-02 | 8,356,235 |
| MAS | koios | 2026-05-01 | 2026-05-02 | 693,498 |
| MAT | koios | 2026-04-30 | 2026-05-01 | 2,533,106 |
| MAT | thundercloud | 2023-01-12 | 2026-03-21 | 13,146 |
| SAM | koios | 2026-02-27 | 2026-02-28 | 117 |
| SEH | koios | 2026-05-01 | 2026-05-02 | 33,538 |
| SHG | koios | 2026-05-01 | 2026-05-02 | 1,611,109 |
| TLH | koios | 2026-04-30 | 2026-05-01 | 1,112,885 |
| TLH | thundercloud | 2021-09-10 | 2026-03-21 | 1,513 |
| UNK | koios | 2024-12-31 | 2026-03-21 | 468 |

## 5. Zero-coverage meters

Total: **221** active meters with no `hourly_consumption` rows for their `account_number`.

First 25 (sorted by site, then account):
| Site | Account | Meter ID | Role | Connect date |
| --- | --- | --- | --- | --- |
| BVM | 0005BVW | BVM00011 | primary | -- |
| GBO | 0006GBO | SMRSD-04-0004F368 | primary | -- |
| GBO | 0012GBO | SMRSD-04-000311D0 | primary | -- |
| GBO | 00190GBO | NOMETER-00190GBO | primary | -- |
| GBO | 0074GlBO | SMRSD-04-00036839 | primary | -- |
| GBO | 0083GBO | SMRSD-04-0009A3FE | primary | -- |
| GBO | 0092GBO | SMRSD-04-0009A428 | primary | -- |
| GBO | 00BO | SMRSD-04-0004F141 | primary | -- |
| GBO | 0119GBO | SMRSD-04-0009A407 | primary | -- |
| GBO | 0125GBO | SMRSD-04-0009A423 | primary | -- |
| GBO | 0131GLBO | SMRSD-04-0009A426 | primary | -- |
| GBO | 0133GBO | SMRSD-04-0009A40C | primary | -- |
| GBO | 0135GBO | SMRSD-04-0009A418 | primary | -- |
| GBO | 0136GBO | SMRSD-04-0009A3E2 | primary | -- |
| GBO | 0137GBO | SMRSD-04-0009A40A | primary | -- |
| GBO | 0138GBO | SMRSD-04-0009A419 | primary | -- |
| GBO | 0139GBO | SMRSD-04-0009A40E | primary | -- |
| GBO | 0146GBO | SMRSD-04-0009A422 | primary | -- |
| GBO | 0188GBO | SMRSD-04-0009A409 | primary | -- |
| GBO | 0189GBO | SMRSD-04-0009A408 | primary | -- |
| GBO | 0191GBO | SMRSD-04-0009A404 | primary | -- |
| GBO | 0192GBO | SMRSD-04-0009A400 | primary | -- |
| GBO | 0194GBO | SMRSD-04-0009A410 | primary | -- |
| GBO | 0195GBO | SMRSD-04-0009A3F7 | primary | -- |
| GBO | 0196GBO | SMRSD-04-0009A41E | primary | -- |

... and 196 more (in JSON output).

## 6. Stale meters (>30 days since last reading)

Total: **363**.

| Site | Account | Meter ID | Last reading | Days stale |
| --- | --- | --- | --- | --- |
| MAK | 0178MAK | ACCT-0178MAK | 2022-03-17 | 1507 |
| MAK | 0203MAK | ACCT-0203MAK | 2022-06-23 | 1408 |
| MAK | 0222MAK | ACCT-0222MAK | 2024-12-30 | 487 |
| MAT | 0048MAT | ACCT-0048MAT | 2025-08-11 | 263 |
| MAT | 0084MAT | ACCT-0084MAT | 2025-10-03 | 211 |
| UNK |  | SMRSD-04-00020DE7 | 2025-10-11 | 202 |
| UNK |  | SMRSD-04-00034CBD | 2025-10-11 | 202 |
| UNK |  | SMRSD-04-00028E27 | 2025-10-11 | 202 |
| SHG | 0011SHG | ACCT-0011SHG | 2025-10-11 | 202 |
| KET | 0118KET | ACCT-0118KET | 2025-12-07 | 145 |
| SHG | 0291SHG | ACCT-0291SHG | 2026-01-07 | 114 |
| MAT | 0096MAT | SMRSD-04-0002DC39 | 2026-01-12 | 110 |
| GBO | 0172GBO | SMRSD-04-000306F6 | 2026-02-18 | 73 |
| GBO | 0176GBO | SMRSD-04-0002E2F5 | 2026-02-18 | 73 |
| GBO | 0072GBO | SMRSD-04-000324E3 | 2026-02-18 | 73 |
| GBO | 0074GBO | SMRSD-04-0002A6A1 | 2026-02-18 | 73 |
| GBO | 2143GBO | SMRSD-04-0004DAF4 | 2026-02-18 | 73 |
| GBO | 0002GBO | SMRSD-04-0004F0D7 | 2026-02-18 | 73 |
| GBO | 0022GBO | SMRSD-04-0004F939 | 2026-02-18 | 73 |
| GBO | 0200GBO | SMRSD-04-0002F565 | 2026-02-18 | 73 |
| GBO | 0167GBO | SMRSD-04-00028892 | 2026-02-18 | 73 |
| GBO | 0127GBO | SMRSD-04-00030ACE | 2026-02-18 | 73 |
| GBO | 0164GBO | SMRSD-04-00037730 | 2026-02-18 | 73 |
| GBO | 0009GBO | SMRSD-04-000310BC | 2026-02-18 | 73 |
| GBO | 0052GBO | SMRSD-04-0002C194 | 2026-02-18 | 73 |

... and 338 more (in JSON output).

## 7. Cross-country meters (wrong DB)

**These meters live in the wrong country DB** -- likely historical migration leak. Investigate and either move or quarantine.

| Foreign site | Meters | Accounts | This DB |
| --- | --- | --- | --- |
| GBO | 135 | 135 | LS |
| SAM | 67 | 67 | LS |


## 8. Sites declared in `country_config` but absent from `hourly_consumption`

| Site | Active meters | Note |
| --- | --- | --- |
| BOB | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| LEB | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| MAN | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| MET | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| NKU | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| RIB | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| SEB | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |
| TOS | 0 | no hourly data ever -- pre-operational, decommissioned, or ingest gap |


## 9. Sites in data but not in `country_config` (orphans)

| Site | Active meters | Note |
| --- | --- | --- |
| UNK | 4 | data present but no country_config entry -- legacy / decommissioned / mystery |

