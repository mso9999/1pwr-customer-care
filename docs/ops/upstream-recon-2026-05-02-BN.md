# 1PDB ↔ upstream reconciliation -- BN (2026-05-02T07:39:08.643553+00:00)

Per (site, month) classification of the gap:

* **we_missed** -- Koios has data, 1PDB doesn't. Re-pull via `import_hourly.py`.
* **we_missed_partial** -- 1PDB has < 50% of upstream samples. Likely partial ingest -- re-pull also recommended.
* **upstream_missing** -- Koios has no data either. Source-side gap; document and move on.
* **match** -- 1PDB ≈ upstream. False positive in the original audit; downstream report logic likely needs investigation.
* **probe_failed** -- couldn't tell (network, auth, rate limit).

## Summary

| Verdict | Count |
|---|---|
| we_missed | 1 |
| we_missed_partial | 0 |
| upstream_missing | 0 |
| match | 2 |
| probe_failed | 0 |

## Per-(site, month) findings

| Site | Month | Verdict | DB rows (month) | Sample days (DB / Koios) | Missed sample days | Note |
|---|---|---|---|---|---|---|
| GBO | 2025-10 | **we_missed** | 51 | 08: 0/53, 16: 0/53, 24: 0/53 | 08, 16, 24 |  |
| SAM | 2025-10 | **match** | 20,257 | 08: 648/54, 16: 712/54, 24: 636/54 | -- |  |
| SAM | 2026-03 | **match** | 22,321 | 08: 738/56, 16: 756/56, 24: 771/56 | -- |  |

## Re-pull recipe

On the production CC host, for each `we_missed` / `we_missed_partial` cell. **Run one at a time** and check the journal (`journalctl -u 1pdb-consumption.service` or just watch stdout) -- Koios has a 30k req/day per-org budget, and a single full month re-pull for one site is ~1500 calls.

```bash
# GBO:2025-10 (we_missed)  -- missed sample days: 2025-10-08, 2025-10-16, 2025-10-24
sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; DATABASE_URL=$DATABASE_URL_BN /opt/cc-portal/backend/venv/bin/python3 /opt/1pdb/services/import_hourly_bn.py 2025-10-01 2025-10-31 --site GBO --no-skip --no-aggregate'

```

After the re-pulls, re-run this reconciliation to confirm verdicts flip to `match`.
Then run `python3 /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py --country LS` to confirm the deficit count drops.
