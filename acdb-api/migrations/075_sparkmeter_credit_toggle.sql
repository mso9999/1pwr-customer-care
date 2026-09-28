-- Per-account override for crediting the SparkMeter when billing is on a 1Meter.
--
-- NULL (automatic): MAK and LAB still push to ThunderCloud, because those
-- sites run a 1Meter and a SparkMeter in series. Any other 1Meter account
-- does not push (the CC ledger is the credit).
-- 'push': always credit ThunderCloud/Koios, including a series pair off MAK/LAB.
-- 'skip': do not credit the SparkMeter when this account bills on a 1Meter.

BEGIN;

ALTER TABLE accounts
  ADD COLUMN IF NOT EXISTS sparkmeter_credit TEXT;

ALTER TABLE accounts
  DROP CONSTRAINT IF EXISTS accounts_sparkmeter_credit_check;

ALTER TABLE accounts
  ADD CONSTRAINT accounts_sparkmeter_credit_check
  CHECK (sparkmeter_credit IS NULL OR sparkmeter_credit IN ('push', 'skip'));

COMMENT ON COLUMN accounts.sparkmeter_credit IS
  'Whether a payment also credits the series SparkMeter. '
  'NULL = automatic (ThunderCloud sites MAK/LAB still push; other 1Meter accounts do not). '
  '''push'' forces the credit. ''skip'' withholds it when billing priority is 1m.';

COMMIT;
