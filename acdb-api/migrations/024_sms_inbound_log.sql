-- Full audit trail of every SMS received from the national gateway.
-- Mirrors sms_outbound_log for the inbound direction.  Stores the
-- complete SMS body so CC never needs to fall back to the PHP
-- gateway's LOGIN.TXT for replay or investigation.

CREATE TABLE IF NOT EXISTS sms_inbound_log (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gateway_msg_id  TEXT,
    sender          TEXT,
    content         TEXT NOT NULL,
    country_code    TEXT,
    parsed_ok       BOOLEAN NOT NULL DEFAULT false,
    parse_result    JSONB,
    account_number  TEXT,
    amount          NUMERIC(12,2),
    receipt_key     TEXT,
    outcome         TEXT,
    error           TEXT,
    transaction_id  BIGINT
);

CREATE INDEX IF NOT EXISTS idx_sms_in_received  ON sms_inbound_log (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_in_sender    ON sms_inbound_log (sender);
CREATE INDEX IF NOT EXISTS idx_sms_in_receipt   ON sms_inbound_log (receipt_key);
CREATE INDEX IF NOT EXISTS idx_sms_in_acct      ON sms_inbound_log (account_number);
CREATE INDEX IF NOT EXISTS idx_sms_in_outcome   ON sms_inbound_log (outcome);
