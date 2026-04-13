-- Customer app / meter-relay care messages (audit + idempotency).
CREATE TABLE IF NOT EXISTS app_care_messages (
    id              BIGSERIAL PRIMARY KEY,
    account_number  TEXT,
    body_text       TEXT NOT NULL,
    category        TEXT,
    source          TEXT NOT NULL DEFAULT 'app',
    device_id       TEXT,
    idempotency_key TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_care_messages_created ON app_care_messages (created_at DESC);
