# Non-Policy Quarantine Registry

Purpose: prevent accidental integration of code that is useful for investigation/recovery
but not part of the current CC<->SM event-parity operating policy.

## Policy Boundary

Current strategy (merge target):
- SM -> CC historical/manual credits mirrored incrementally by watermark.
- CC -> SM credits delivered through durable retry queue.
- Drift managed by event-parity operations, not recurring reseed mechanics.

Out of policy for this strategy:
- New recurring seed/reseed flows.
- One-off host patch scripts as normal deployment path.
- Discovery/probe scripts as production tooling.
- Domain-expansion work not tied to parity (for example inverter adapter expansion).

## Quarantined Sets

### A) Recovery/Reseed tooling (ops-only, explicit human approval)
- `scripts/ops/reconcile_seed_at_first_gap.py`
- `scripts/ops/rollback_seed_batch.py`
- `scripts/ops/rollback_hist_repair_range.py`
- `scripts/ops/archive_and_purge_seed_prefixes.py`
- `scripts/ops/inventory_seed_prefixes.py`
- `scripts/ops/verify_seed_cleanup.py`
- `scripts/ops/backfill_merchant_payments_from_exports.py`

### B) Koios customer/probe experiments (not parity core)
- `acdb-api/sparkmeter_customer.py`
- `scripts/ops/deploy_koios_web_session.sh`
- `scripts/ops/fix_audit_noncommissioned.py`
- `scripts/ops/sm_resilience_patch.py`
- `scripts/ops/koios_browser_create_customer.py`
- `scripts/ops/koios_web_create_customer.py`
- `scripts/ops/koios_ui_discover.py`
- `scripts/ops/probe_koios_payment_endpoints.py`
- `scripts/ops/probe_koios_payments_filters.py`
- `scripts/ops/probe_koios_web.py`
- `scripts/ops/probe_koios_web2.py`
- `scripts/ops/probe_koios_web3.py`
- `scripts/ops/probe_koios_web4.py`
- `scripts/ops/probe_koios_web5.py`

### C) Non-parity domain expansion (separate stream)
- `acdb-api/gensite/adapters/__init__.py`
- `acdb-api/gensite/adapters/alphaess.py`
- `acdb-api/gensite/adapters/sinosoar.py`
- `acdb-api/gensite/adapters/solarman.py`

## Integration Guardrails

1. Quarantined files must land only via dedicated PRs with explicit intent
   in title/body (for example: "ops recovery tooling" or "gensite adapters").
2. Do not mix quarantined code into event-parity PRs.
3. If a quarantined file must be promoted, add/update tests and update this
   registry in the same PR.
4. Keep local artifacts and probe outputs out of staging by explicit-path `git add`.

## Related docs
- `docs/ops/event-parity-quarantine-plan-2026-05-25.md`
- `scripts/ops/QUARANTINED_NON_POLICY.md`
- `acdb-api/QUARANTINED_NON_POLICY.md`

