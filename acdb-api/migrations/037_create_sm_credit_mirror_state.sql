-- Persist incremental watermark state for SM->CC credit mirror jobs.
CREATE TABLE IF NOT EXISTS sm_credit_mirror_state (
    country_code      TEXT NOT NULL,
    platform          TEXT NOT NULL CHECK (platform IN ('koios', 'thundercloud')),
    last_credited_at  TIMESTAMPTZ,
    last_external_id  TEXT NOT NULL DEFAULT '',
    last_run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_status       TEXT NOT NULL DEFAULT 'unknown',
    last_message      TEXT NOT NULL DEFAULT '',
    last_candidates   INTEGER NOT NULL DEFAULT 0,
    last_inserted     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (country_code, platform)
);

