# 1PDB ↔ upstream reconciliation -- LS (2026-05-02T07:33:34.733767+00:00)

Per (site, month) classification of the gap:

* **we_missed** -- Koios has data, 1PDB doesn't. Re-pull via `import_hourly.py`.
* **we_missed_partial** -- 1PDB has < 50% of upstream samples. Likely partial ingest -- re-pull also recommended.
* **upstream_missing** -- Koios has no data either. Source-side gap; document and move on.
* **match** -- 1PDB ≈ upstream. False positive in the original audit; downstream report logic likely needs investigation.
* **probe_failed** -- couldn't tell (network, auth, rate limit).

## Summary

| Verdict | Count |
|---|---|
| we_missed | 6 |
| we_missed_partial | 8 |
| upstream_missing | 0 |
| match | 1 |
| probe_failed | 0 |

## Per-(site, month) findings

| Site | Month | Verdict | DB rows (month) | Sample days (DB / Koios) | Missed sample days | Note |
|---|---|---|---|---|---|---|
| KET | 2026-01 | **we_missed** | 4,525 | 08: 0/137, 16: 0/133, 24: 0/135 | 08, 16, 24 |  |
| KET | 2026-03 | **we_missed** | 2,351 | 08: 0/148, 16: 0/148, 24: 0/148 | 08, 16, 24 |  |
| LSB | 2026-02 | **we_missed_partial** | 2,324 | 08: 0/24, 15: 568/24, 22: 0/25 | 08, 22 |  |
| LSB | 2026-03 | **we_missed** | 19 | 08: 0/25, 16: 0/23, 24: 0/25 | 08, 16, 24 |  |
| LSB | 2026-04 | **we_missed** | 2,315 | 08: 0/25, 16: 0/24, 23: 0/25 | 08, 16, 23 |  |
| MAS | 2025-12 | **we_missed_partial** | 3,618 | 08: 168/168, 16: 182/182, 24: 0/183 | 24 |  |
| MAS | 2026-01 | **we_missed_partial** | 3,835 | 08: 182/182, 16: 184/184, 24: 0/183 | 24 |  |
| MAT | 2026-01 | **we_missed_partial** | 38,513 | 08: 256/256, 16: 0/257, 24: 0/256 | 16, 24 |  |
| MAT | 2026-02 | **we_missed** | 20,987 | 08: 0/257, 15: 0/256, 22: 0/258 | 08, 15, 22 |  |
| MAT | 2026-03 | **we_missed_partial** | 4,876 | 08: 0/256, 16: 2,394/257, 24: 0/256 | 08, 24 |  |
| MAT | 2026-04 | **we_missed_partial** | 40,864 | 08: 2,673/260, 16: 0/261, 23: 2,712/260 | 16 |  |
| SEH | 2026-01 | **we_missed** | 343 | 08: 0/7, 16: 0/7, 24: 0/7 | 08, 16, 24 |  |
| SEH | 2026-03 | **match** | 222 | 08: 7/7, 16: 7/7, 24: 7/7 | -- |  |
| SHG | 2026-02 | **we_missed_partial** | 39,188 | 08: 0/271, 15: 0/301, 22: 2,697/289 | 08, 15 |  |
| TLH | 2026-02 | **we_missed_partial** | 5,657 | 08: 0/72, 15: 0/74, 22: 771/71 | 08, 15 |  |

## Re-pull recipe

On the production CC host, for each `we_missed` / `we_missed_partial` cell. **Run one at a time** and check the journal (`journalctl -u 1pdb-consumption.service` or just watch stdout) -- Koios has a 30k req/day per-org budget, and a single full month re-pull for one site is ~1500 calls.

```bash
# KET:2026-01 (we_missed)  -- missed sample days: 2026-01-08, 2026-01-16, 2026-01-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-01-01 2026-01-31 --country LS --site KET --no-skip --no-aggregate'

# KET:2026-03 (we_missed)  -- missed sample days: 2026-03-08, 2026-03-16, 2026-03-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-03-01 2026-03-31 --country LS --site KET --no-skip --no-aggregate'

# LSB:2026-02 (we_missed_partial)  -- missed sample days: 2026-02-08, 2026-02-22
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-02-01 2026-02-28 --country LS --site LSB --no-skip --no-aggregate'

# LSB:2026-03 (we_missed)  -- missed sample days: 2026-03-08, 2026-03-16, 2026-03-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-03-01 2026-03-31 --country LS --site LSB --no-skip --no-aggregate'

# LSB:2026-04 (we_missed)  -- missed sample days: 2026-04-08, 2026-04-16, 2026-04-23
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-04-01 2026-04-30 --country LS --site LSB --no-skip --no-aggregate'

# MAS:2025-12 (we_missed_partial)  -- missed sample days: 2025-12-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2025-12-01 2025-12-31 --country LS --site MAS --no-skip --no-aggregate'

# MAS:2026-01 (we_missed_partial)  -- missed sample days: 2026-01-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-01-01 2026-01-31 --country LS --site MAS --no-skip --no-aggregate'

# MAT:2026-01 (we_missed_partial)  -- missed sample days: 2026-01-16, 2026-01-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-01-01 2026-01-31 --country LS --site MAT --no-skip --no-aggregate'

# MAT:2026-02 (we_missed)  -- missed sample days: 2026-02-08, 2026-02-15, 2026-02-22
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-02-01 2026-02-28 --country LS --site MAT --no-skip --no-aggregate'

# MAT:2026-03 (we_missed_partial)  -- missed sample days: 2026-03-08, 2026-03-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-03-01 2026-03-31 --country LS --site MAT --no-skip --no-aggregate'

# MAT:2026-04 (we_missed_partial)  -- missed sample days: 2026-04-16
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-04-01 2026-04-30 --country LS --site MAT --no-skip --no-aggregate'

# SEH:2026-01 (we_missed)  -- missed sample days: 2026-01-08, 2026-01-16, 2026-01-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-01-01 2026-01-31 --country LS --site SEH --no-skip --no-aggregate'

# SHG:2026-02 (we_missed_partial)  -- missed sample days: 2026-02-08, 2026-02-15
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-02-01 2026-02-28 --country LS --site SHG --no-skip --no-aggregate'

# TLH:2026-02 (we_missed_partial)  -- missed sample days: 2026-02-08, 2026-02-15
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly.py 2026-02-01 2026-02-28 --country LS --site TLH --no-skip --no-aggregate'

```

After the re-pulls, re-run this reconciliation to confirm verdicts flip to `match`.
Then run `python3 /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py --country LS` to confirm the deficit count drops.

## ThunderCloud parquet inventory (MAK)

* TC has parquets for **274** days in the audit window.
* 1PDB has hourly rows for **274** days.
* Days covered in both: **274**.

**No days where TC has data and 1PDB doesn't.** ThunderCloud → 1PDB pipe is faithful in this window.

