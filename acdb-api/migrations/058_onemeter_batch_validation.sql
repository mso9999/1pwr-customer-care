-- The relay API has always recorded the customer-facing command/platform
-- aliases as well as the firmware action. Converge clean databases and older
-- partial production schemas before the validation workflow inserts commands.
ALTER TABLE relay_commands ADD COLUMN IF NOT EXISTS command TEXT;
ALTER TABLE relay_commands ADD COLUMN IF NOT EXISTS platform TEXT;

CREATE TABLE IF NOT EXISTS onemeter_validation_sessions (
    id                    UUID PRIMARY KEY,
    batch_reference       TEXT NOT NULL,
    thing_name            TEXT NOT NULL,
    meter_id              TEXT NOT NULL,
    site_code             TEXT NOT NULL,
    dummy_customer_label  TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'started'
                          CHECK (status IN ('started', 'load_seen', 'disconnected',
                                            'reconnected', 'passed', 'failed')),
    simulated_balance_kwh NUMERIC(14,6) NOT NULL DEFAULT 0,
    baseline_energy_kwh   NUMERIC(16,6),
    latest_energy_kwh     NUMERIC(16,6),
    load_delta_kwh        NUMERIC(14,6) NOT NULL DEFAULT 0,
    disconnect_cmd_id     UUID,
    reconnect_cmd_id      UUID,
    created_by            TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at          TIMESTAMPTZ,
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_onemeter_validation_thing_created
    ON onemeter_validation_sessions (thing_name, created_at DESC);

CREATE TABLE IF NOT EXISTS onemeter_validation_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES onemeter_validation_sessions(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    amount_kwh  NUMERIC(14,6),
    energy_kwh  NUMERIC(16,6),
    cmd_id      UUID,
    details     JSONB,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_onemeter_validation_events_session
    ON onemeter_validation_events (session_id, created_at);
