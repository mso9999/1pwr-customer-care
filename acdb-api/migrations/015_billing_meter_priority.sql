-- 015_billing_meter_priority.sql
--
-- Toggleable billing-source primacy for the SM-to-1M billing migration test.
-- See docs/ops/1meter-billing-migration-protocol.md for the full protocol.
--
-- Resolution precedence used by balance_engine._resolve_billing_priority:
--   1. accounts.billing_meter_priority  (per-account override; NULL = use default)
--   2. system_config(key='billing_meter_priority')  (fleet-wide default)
--   3. Hardcoded 'sm' fallback in code
--
-- Phase 1 (today): everyone defaults to 'sm'. Per-account flips during the
--   test population's rollout will use this column. Auditable via cc_mutations.
-- Phase 2:  fleet default flipped to '1m' once Phase 1 exit criteria met.
-- Phase 3:  SM rows in hourly_consumption stop being written; no schema change
--   needed (the priority-aware query falls back to whichever source has data).

BEGIN;

-- 1. Fleet-wide default
INSERT INTO system_config (key, value)
VALUES ('billing_meter_priority', 'sm')
ON CONFLICT (key) DO NOTHING;

-- 2. Per-account override
ALTER TABLE accounts
  ADD COLUMN IF NOT EXISTS billing_meter_priority TEXT;

ALTER TABLE accounts
  DROP CONSTRAINT IF EXISTS accounts_billing_meter_priority_check;
ALTER TABLE accounts
  ADD CONSTRAINT accounts_billing_meter_priority_check
  CHECK (billing_meter_priority IS NULL OR billing_meter_priority IN ('sm', '1m'));

COMMENT ON COLUMN accounts.billing_meter_priority IS
  'Per-account override for which meter source is authoritative for kWh balance: '
  'sm (SparkMeter via thundercloud/koios) or 1m (1Meter prototype via iot). '
  'NULL means inherit the fleet default from system_config(billing_meter_priority). '
  'Changes are audited via cc_mutations.';

COMMIT;
