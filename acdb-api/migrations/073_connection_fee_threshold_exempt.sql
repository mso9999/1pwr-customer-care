-- Per-account exemption from the optional country connection-fee threshold.
-- The threshold itself lives in system_config.connection_fee_threshold and
-- stays unset until finance sets it. Missing or false means the threshold
-- rule applies when that country value is set. Exact-amount fee matching
-- is unchanged.

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS fee_threshold_exempt BOOLEAN NOT NULL DEFAULT FALSE;
