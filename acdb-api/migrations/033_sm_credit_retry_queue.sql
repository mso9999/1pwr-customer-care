-- Durable retry queue for CC -> SparkMeter/Koios credit pushes.
CREATE TABLE IF NOT EXISTS sm_credit_retry_queue (
    id                  BIGSERIAL PRIMARY KEY,
    account_number      TEXT NOT NULL,
    amount              NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    memo                TEXT NOT NULL DEFAULT '',
    external_id         TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'retrying', 'done', 'failed')),
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    first_error         TEXT,
    last_error          TEXT,
    last_attempt_at     TIMESTAMPTZ,
    next_retry_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ
);

-- External id is typically the transaction id from 1PDB and should be unique.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sm_credit_retry_external
    ON sm_credit_retry_queue (external_id)
    WHERE external_id IS NOT NULL AND btrim(external_id) <> '';

CREATE INDEX IF NOT EXISTS idx_sm_credit_retry_due
    ON sm_credit_retry_queue (status, next_retry_at);
