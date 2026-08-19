-- Migration 066: Steamaco enum/constraint alignment
--
-- Migration 015's CHECK constraint only allowed ('sm', '1m'), and the
-- meters.platform enum (meter_platform) only knew sparkmeter/prototype/legacy.
-- The Steamaco clinic integration (065) needs 'steamaco' in both.

ALTER TYPE meter_platform ADD VALUE IF NOT EXISTS 'steamaco';

BEGIN;

ALTER TABLE accounts
  DROP CONSTRAINT IF EXISTS accounts_billing_meter_priority_check;
ALTER TABLE accounts
  ADD CONSTRAINT accounts_billing_meter_priority_check
  CHECK (billing_meter_priority IS NULL OR billing_meter_priority IN ('sm', '1m', 'steamaco'));

COMMIT;
