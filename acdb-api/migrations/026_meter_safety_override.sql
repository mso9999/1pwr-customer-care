-- Safety override: ops team can force a meter's relay open regardless of credit balance.
-- NULL = auto (normal billing), 'off' = override active (relay forced open, auto-trigger suppressed)
BEGIN;

ALTER TABLE meters ADD COLUMN IF NOT EXISTS safety_override VARCHAR(16);

ALTER TABLE meters DROP CONSTRAINT IF EXISTS chk_meters_safety_override;

ALTER TABLE meters ADD CONSTRAINT chk_meters_safety_override
  CHECK (safety_override IS NULL OR safety_override IN ('off'));

ALTER TABLE meters ADD COLUMN IF NOT EXISTS safety_override_by TEXT;

ALTER TABLE meters ADD COLUMN IF NOT EXISTS safety_override_at TIMESTAMPTZ;

COMMIT;
