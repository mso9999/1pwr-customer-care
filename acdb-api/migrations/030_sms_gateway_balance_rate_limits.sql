-- SMS: gateway balance callback rate limits + low-balance alerts per calendar day.
-- Defaults are inserted for new installs; existing rows unchanged on conflict.

BEGIN;

CREATE TABLE IF NOT EXISTS sms_gateway_balance_rate_log (
    id              BIGSERIAL PRIMARY KEY,
    rate_key        TEXT NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sms_gw_bal_rate_key_time
    ON sms_gateway_balance_rate_log (rate_key, requested_at DESC);

COMMENT ON TABLE sms_gateway_balance_rate_log IS
    'One row per allowed GET /api/payments/gateway/balance* call for rate limiting (per account or per phone).';

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS low_balance_alerts_local_date DATE NULL,
    ADD COLUMN IF NOT EXISTS low_balance_alerts_sent_today INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN accounts.low_balance_alerts_local_date IS
    'Calendar date (country timezone) for which low_balance_alerts_sent_today applies.';
COMMENT ON COLUMN accounts.low_balance_alerts_sent_today IS
    'Number of low-balance SMS sent on low_balance_alerts_local_date (capped by system_config).';

INSERT INTO system_config (key, value)
SELECT 'sms_balance_reply_max_per_hour', '1'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'sms_balance_reply_max_per_hour');
INSERT INTO system_config (key, value)
SELECT 'sms_balance_reply_max_per_day', '3'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'sms_balance_reply_max_per_day');
INSERT INTO system_config (key, value)
SELECT 'low_balance_alert_max_per_day', '2'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'low_balance_alert_max_per_day');

COMMIT;
