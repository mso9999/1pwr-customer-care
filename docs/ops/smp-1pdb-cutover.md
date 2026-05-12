# SMP 1PDB–SparkMeter cutover

Lesotho **Sotho Minigrid Portfolio (SMP)** uses **1PDB** as the canonical customer ledger and balance after cutover. SparkMeter (Koios + ThunderCloud) mirrors **new** electricity credits from Customer Care and continues to supply **consumption** reads into `hourly_consumption`.

## Steady-state data flow

- **Payments:** SMS / portal / webhook → `transactions` in 1PDB (`record_payment_kwh`) → `credit_sparkmeter` to Koios or ThunderCloud.
- **Consumption:** Koios hourly import + ThunderCloud live readings → `hourly_consumption` (no payment credit).
- **Balance:** `balance_engine.get_balance_kwh()` — not SparkMeter customer lookup for customer-facing text.

## One-time cutover

At **T_cutover** the **meter balance is authoritative** for the ending balance on each in-scope account. CC inserts a single tagged `balance_seed` row per account where `kwh_value = SM_kWh − 1PDB_kWh`. Merchant `mm:%` rows remain audit-only (`kwh_value` NULL).

### Bulk exclusions

- `0500MAK`, `*BVW`, **LAB** (not a real site), `FAULTY*`, invalid account codes.
- Handle excluded accounts manually.

### Negative delta policy

Do not bulk-seed when **1PDB > SM** until consumption ingest gaps are ruled out (`audit_upstream_reconciliation.py`). Use `cutover_ls_balances.py --allow-negative-delta` only with finance approval.

### Tooling (CC host)

| Step | Command |
|------|---------|
| Preflight + finance CSV | `PYTHONPATH=/opt/cc-portal/backend ./venv/bin/python3 scripts/ops/preflight_smp_cutover.py` |
| Optional historical repair | `.../repair_historical_payment_credits.py --report-csv /tmp/hist_payment_repair.csv` then `--apply` |
| Cutover preview | `.../cutover_ls_balances.py --preview-csv /tmp/smp_cutover_preview.csv` |
| Cutover apply | `.../cutover_ls_balances.py --apply --cutover-tag smp_cutover_YYYY-MM-DD` |
| Post-cutover monitor | `audit_ls_balances.py --check` (systemd `cc-ls-balance-audit.timer`) |

Run as `cc_api` with `source /opt/1pdb/.env`.

### ThunderCloud payment import after cutover

Set `LS_SMP_CUTOVER_AT` (ISO date/time) and `TC_IMPORT_RECONCILE_ONLY=1` on the host running `import_tc_transactions.py`. Post-cutover TC credits are logged, not inserted into `transactions`.

## Rollback

Delete only `balance_seed` rows with `payment_reference` matching `smp_cutover_*` if no subsequent live payments depend on them. Otherwise restore from [`postgres-backup-recovery.md`](postgres-backup-recovery.md).

Merchant backfill rows: `source_table LIKE 'mm:%'` — separate from cutover seeds.

## Success criteria

- In-scope accounts: `|SM − 1PDB| < 0.5 kWh` immediately after cutover.
- Daily `audit_ls_balances.py --check` clean for seven days.
- New payments: single credited path (1PDB → SM).
