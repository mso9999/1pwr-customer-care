-- Track every outbound SMS sent through the PHP gateway.
CREATE TABLE IF NOT EXISTS sms_outbound_log (
    id              BIGSERIAL PRIMARY KEY,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sms_type        TEXT NOT NULL DEFAULT 'balance',
    phone_raw       TEXT,
    phone_normalized TEXT,
    message         TEXT NOT NULL,
    success         BOOLEAN NOT NULL DEFAULT false,
    error           TEXT,
    account_number  TEXT,
    trigger_ctx     TEXT,
    gateway_url     TEXT
);

CREATE INDEX IF NOT EXISTS idx_sms_log_sent_at ON sms_outbound_log (sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_log_type    ON sms_outbound_log (sms_type);
CREATE INDEX IF NOT EXISTS idx_sms_log_success ON sms_outbound_log (success);
CREATE INDEX IF NOT EXISTS idx_sms_log_phone  ON sms_outbound_log (phone_normalized);
CREATE INDEX IF NOT EXISTS idx_sms_log_acct   ON sms_outbound_log (account_number);
