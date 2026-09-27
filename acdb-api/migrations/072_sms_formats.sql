-- Operator-editable SMS formats (payment confirmations + balance requests).
--
-- The national SMS gateways are pass-through: they forward every SMS to
-- POST /api/sms/incoming and CC decides what it is. Formats in this table are
-- tried before the built-in parsers (mpesa_sms.py / momo_bj.py), so O&M / IT /
-- admin can add a new provider (e.g. Moov Money, Celtiis Cash) or a changed
-- template without a code deploy. Each country backend has its own database,
-- so rows are per country; country_code is kept as a guard.

CREATE TABLE IF NOT EXISTS sms_formats (
    id                  BIGSERIAL PRIMARY KEY,
    country_code        TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'payment'
                        CHECK (kind IN ('payment', 'balance_request')),
    provider            TEXT NOT NULL,
    name                TEXT NOT NULL,
    pattern_type        TEXT NOT NULL DEFAULT 'template'
                        CHECK (pattern_type IN ('template', 'regex')),
    pattern             TEXT NOT NULL,
    sender_pattern      TEXT,
    decimal_separator   TEXT NOT NULL DEFAULT '.'
                        CHECK (decimal_separator IN ('.', ',')),
    phone_prefix        TEXT,
    priority            INTEGER NOT NULL DEFAULT 100,
    enabled             BOOLEAN NOT NULL DEFAULT false,
    samples             JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes               TEXT,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sms_formats_active
    ON sms_formats (country_code, kind, enabled, priority);

CREATE TABLE IF NOT EXISTS sms_format_audit (
    id          BIGSERIAL PRIMARY KEY,
    format_id   BIGINT,
    action      TEXT NOT NULL,
    actor       TEXT,
    before      JSONB,
    after       JSONB,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sms_format_audit_format ON sms_format_audit (format_id, at DESC);

-- Settings (off by default so nothing changes until an operator opts in).
INSERT INTO system_config (key, value) VALUES
    ('sms_trusted_senders', ''),
    ('sms_trusted_senders_mode', 'off'),
    ('sms_balance_replies_enabled', '0')
ON CONFLICT (key) DO NOTHING;
