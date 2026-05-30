# `scripts/ops` Quarantined Non-Policy Code

This file marks scripts that are intentionally **not** part of the current
event-parity strategy baseline.

If you are preparing a parity PR, do not stage these unless the PR explicitly
targets their domain.

## Quarantined in this directory

- Seed/recovery tooling:
  - `reconcile_seed_at_first_gap.py`
  - `rollback_seed_batch.py`
  - `rollback_hist_repair_range.py`
  - `archive_and_purge_seed_prefixes.py`
  - `inventory_seed_prefixes.py`
  - `verify_seed_cleanup.py`
  - `backfill_merchant_payments_from_exports.py`
- Koios exploration/patch tooling:
  - `deploy_koios_web_session.sh`
  - `fix_audit_noncommissioned.py`
  - `sm_resilience_patch.py`
  - `koios_browser_create_customer.py`
  - `koios_web_create_customer.py`
  - `koios_ui_discover.py`
  - `probe_koios_payment_endpoints.py`
  - `probe_koios_payments_filters.py`
  - `probe_koios_web.py`
  - `probe_koios_web2.py`
  - `probe_koios_web3.py`
  - `probe_koios_web4.py`
  - `probe_koios_web5.py`

## Baseline parity scripts in this directory

- `import_sm_manual_credits.py`
- `run_sm_credit_mirror_incremental.py`
- `inspect_sm_manual_hist.py`

See `docs/ops/non-policy-quarantine-registry.md` for cross-directory policy.

