-- Track actual CM.com delivery status for each outbound SMS.
ALTER TABLE sms_outbound_log
    ADD COLUMN IF NOT EXISTS cm_status     TEXT,
    ADD COLUMN IF NOT EXISTS cm_status_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cm_error_code INTEGER;

CREATE INDEX IF NOT EXISTS idx_sms_log_cm_status ON sms_outbound_log (cm_status);
