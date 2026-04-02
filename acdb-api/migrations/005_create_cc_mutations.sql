BEGIN;

CREATE TABLE IF NOT EXISTS cc_mutations (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_type           TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    user_name           TEXT NOT NULL DEFAULT '',
    actor_role          TEXT NOT NULL DEFAULT '',
    action              TEXT NOT NULL,
    table_name          TEXT NOT NULL,
    record_id           TEXT NOT NULL,
    old_values          JSONB,
    new_values          JSONB,
    event_metadata      JSONB,
    reverts_mutation_id BIGINT REFERENCES cc_mutations(id),
    source_system       TEXT NOT NULL DEFAULT 'cc_api',
    source_mutation_id  BIGINT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_cc_mutations_timestamp
    ON cc_mutations (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_cc_mutations_table_record
    ON cc_mutations (table_name, record_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_cc_mutations_user
    ON cc_mutations (user_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_cc_mutations_action
    ON cc_mutations (action, id DESC);

CREATE INDEX IF NOT EXISTS idx_cc_mutations_reverts
    ON cc_mutations (reverts_mutation_id);

COMMIT;
