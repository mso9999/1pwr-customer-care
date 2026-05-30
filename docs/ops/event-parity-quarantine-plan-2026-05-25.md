# Event-Parity Quarantine Plan (2026-05-25)

This plan quarantines non-core changes by PR scope, without deleting local artifacts.

Related governance:
- `docs/ops/non-policy-quarantine-registry.md` (cross-directory quarantine policy)
- `scripts/ops/QUARANTINED_NON_POLICY.md`
- `acdb-api/QUARANTINED_NON_POLICY.md`

## In-Scope (Event Parity Core)

These files form the merge-ready event-parity slice:

- `acdb-api/crud.py`
- `scripts/ops/import_sm_manual_credits.py`
- `scripts/ops/run_sm_credit_mirror_incremental.py`
- `scripts/ops/inspect_sm_manual_hist.py`
- `acdb-api/migrations/037_create_sm_credit_mirror_state.sql`
- `deploy/systemd/cc-sm-credit-mirror.service`
- `deploy/systemd/cc-sm-credit-mirror.timer`

## Quarantined (Useful, Separate Scope)

Keep these out of the event-parity PR unless explicitly requested:

- Recovery/seed tooling:
  - `scripts/ops/archive_and_purge_seed_prefixes.py`
  - `scripts/ops/inventory_seed_prefixes.py`
  - `scripts/ops/verify_seed_cleanup.py`
  - `scripts/ops/rollback_seed_batch.py`
  - `scripts/ops/rollback_hist_repair_range.py`
  - `scripts/ops/reconcile_seed_at_first_gap.py`
  - `scripts/ops/backfill_merchant_payments_from_exports.py`
- Koios customer provisioning experiments/ops:
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
- Gensite adapters (separate domain):
  - `acdb-api/gensite/adapters/__init__.py`
  - `acdb-api/gensite/adapters/alphaess.py`
  - `acdb-api/gensite/adapters/sinosoar.py`
  - `acdb-api/gensite/adapters/solarman.py`
- Docs/tutorial/context scope:
  - `CONTEXT.md`
  - `SESSION_LOG.md`
  - `acdb-api/frontend/src/i18n/en/tutorial.json`
  - `acdb-api/frontend/src/i18n/fr/tutorial.json`
  - `acdb-api/frontend/src/pages/tutorialWorkflows.ts`

## Local Artifacts (Retain Locally, Keep Out of PR)

Do not delete; keep as local references unless explicitly requested:

- `acdb-api/cc_auth.db`
- `missing_sm_customers.csv`
- `docs/260524 OTA 1M FW.rtf`
- `docs/DEYE API Documentation.rtf`
- `docs/DEYE API.rtf`
- `docs/KOIOS API v2.rtf`
- `docs/KOIOS API.rtf`
- `docs/TC API.rtf`
- `docs/Untitled 102.rtf`
- `docs/export_messages (3).csv`

Also retain but ignore in PR scope:

- malformed duplicate path artifact:
  - `"/Users/mattmso/Dropbox/AI Projects/1PWR CC/scripts/ops/koios_browser_create_customer.py"`

## Safe Staging Recipe (Core Only)

Use explicit-path staging to keep quarantine boundaries:

```bash
git add -- \
  acdb-api/crud.py \
  scripts/ops/import_sm_manual_credits.py \
  scripts/ops/run_sm_credit_mirror_incremental.py \
  scripts/ops/inspect_sm_manual_hist.py \
  acdb-api/migrations/037_create_sm_credit_mirror_state.sql \
  deploy/systemd/cc-sm-credit-mirror.service \
  deploy/systemd/cc-sm-credit-mirror.timer \
  docs/ops/event-parity-quarantine-plan-2026-05-25.md
```

Verify staged content:

```bash
git diff --staged --name-only
```

Expected output should only include the core files above.

