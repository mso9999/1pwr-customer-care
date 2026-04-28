-- 016_relay_commands.sql
--
-- Audit + state table for CC -> 1Meter relay commands.
-- See docs/ops/1meter-billing-migration-protocol.md (Phase 2: Relay command channel).
--
-- Lifecycle: a row is created (status='queued') the moment CC publishes the
-- command to AWS IoT. The firmware acks via MQTT; ingestion_gate forwards the
-- ack to POST /api/meters/relay-ack which moves the row to status='acked'
-- (and 'completed' once relay_after is recorded). Rows stuck in 'queued'
-- past their TTL are 'timed_out' by a sweeper.
--
-- Auto-trigger gate: in Phase 1 the relay channel is exercisable manually
-- (POST /api/meters/{thing}/relay) but balance-zero auto-cutoff is gated by
-- env RELAY_AUTO_TRIGGER_ENABLED=1, off by default.

BEGIN;

CREATE TABLE IF NOT EXISTS relay_commands (
    id              BIGSERIAL PRIMARY KEY,
    cmd_id          UUID NOT NULL UNIQUE,                     -- echoed in MQTT payload + ack
    thing_name      TEXT NOT NULL,                            -- AWS IoT thing name (e.g. OneMeter13)
    meter_id        TEXT,                                     -- short id (e.g. 23022673), best-effort
    account_number  TEXT,                                     -- best-effort (NULL for infra meters)
    action          TEXT NOT NULL CHECK (action IN ('open', 'close')),
    reason          TEXT NOT NULL,                            -- e.g. zero_balance, manual_override, test
    requested_by    TEXT NOT NULL,                            -- user_id or 'auto:zero_balance'
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_seconds     INTEGER NOT NULL DEFAULT 300,             -- firmware ignores expired commands
    published_at    TIMESTAMPTZ,                              -- IoT publish ack
    acked_at        TIMESTAMPTZ,                              -- firmware ack received
    relay_after     TEXT,                                     -- relay state reported in ack ('1' / '0')
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'published', 'acked', 'completed', 'timed_out', 'rejected', 'failed')),
    error           TEXT,
    payload         JSONB,                                    -- full request body (audit)
    ack_payload     JSONB,                                    -- full ack body (audit)
    cc_mutation_id  BIGINT REFERENCES cc_mutations(id)
);

CREATE INDEX IF NOT EXISTS idx_relay_commands_thing_requested
    ON relay_commands (thing_name, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_relay_commands_account
    ON relay_commands (account_number, requested_at DESC)
    WHERE account_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_relay_commands_open
    ON relay_commands (thing_name, requested_at DESC)
    WHERE status IN ('queued', 'published');

COMMENT ON TABLE  relay_commands               IS 'Audit + state for CC->1Meter relay commands; one row per command.';
COMMENT ON COLUMN relay_commands.cmd_id        IS 'UUID published in the MQTT payload and echoed in the ack; firmware dedupes on this.';
COMMENT ON COLUMN relay_commands.ttl_seconds   IS 'Firmware drops commands whose (now - requested_at) > ttl_seconds; defends against late delivery after mesh reroute.';
COMMENT ON COLUMN relay_commands.cc_mutation_id IS 'Linked cc_mutations row for the command request (paired audit trail).';

COMMIT;
