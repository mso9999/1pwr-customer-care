-- 045_lpg_tracking.sql
--
-- LPG (generator fuel) inventory + generator-run tracking.
--
-- Operations created a flow (flowchart, 2026-06-23) to track LPG consumption,
-- balance and cost per site for daily ops and reporting. This schema backs it.
--
-- Model:
--   * Stock capture: each delivery is a BATCH of N x 48 kg cylinders with an
--     auto-generated batch number and a per-cylinder price. cylinders_remaining
--     starts equal to cylinders_total.
--   * Generator run: an operator logs START (battery SOC, reason, operator,
--     instructor = CC user, against a batch). A timer runs until STOP, when the
--     stoppage data is captured. If a cylinder was emptied during the run the
--     batch's cylinders_remaining is decremented. When a batch reaches its last
--     cylinder (remaining = 1) the site is flagged CRITICAL and an O&M alert is
--     fired once (critical_alert_sent_at dedupes it).
--   * Cost: unit_price is the price of ONE 48 kg cylinder; cylinder cost for a
--     period = cylinders consumed x unit_price (in the batch's currency).
--
-- Tables both key to sites(code) (master site list from 013_gensite_equipment).
-- Deploy: auto-applied by CI (.github/workflows/deploy.yml) against each
-- country DB (onepower_cc / onepower_bj / onepower_zm).

-- ---------------------------------------------------------------------------
-- updated_at trigger function (self-contained; idempotent)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lpg_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- lpg_batches  — one row per LPG delivery (stock capture)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lpg_batches (
    id                     BIGSERIAL PRIMARY KEY,
    site_code              TEXT NOT NULL REFERENCES sites(code) ON DELETE RESTRICT,
    batch_number           TEXT NOT NULL,                     -- e.g. LPG-0026MAK-20260623-01
    arrived_at             TIMESTAMPTZ NOT NULL,
    cylinders_total        INTEGER NOT NULL CHECK (cylinders_total > 0),
    cylinders_remaining    INTEGER NOT NULL CHECK (cylinders_remaining >= 0),
    cylinder_kg            NUMERIC(7, 2) NOT NULL DEFAULT 48, -- nominal cylinder size
    unit_price             NUMERIC(12, 2),                    -- price of ONE cylinder
    currency               TEXT,                              -- LSL | XOF | ZMW
    status                 TEXT NOT NULL DEFAULT 'active',    -- active | depleted | archived
    critical_alert_sent_at TIMESTAMPTZ,                       -- dedupes the remaining=1 alert
    created_by             TEXT,
    notes                  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lpg_batches_number UNIQUE (batch_number),
    CONSTRAINT ck_lpg_batches_remaining_le_total
        CHECK (cylinders_remaining <= cylinders_total)
);

CREATE INDEX IF NOT EXISTS idx_lpg_batches_site        ON lpg_batches (site_code);
CREATE INDEX IF NOT EXISTS idx_lpg_batches_site_status ON lpg_batches (site_code, status);
CREATE INDEX IF NOT EXISTS idx_lpg_batches_active      ON lpg_batches (site_code)
    WHERE status = 'active';

COMMENT ON TABLE  lpg_batches                      IS 'LPG deliveries (stock capture). One row per batch of 48kg cylinders.';
COMMENT ON COLUMN lpg_batches.unit_price           IS 'Price of a single cylinder; cost = cylinders consumed x unit_price.';
COMMENT ON COLUMN lpg_batches.cylinders_remaining  IS 'Cylinders left in this batch; decremented when a run depletes a cylinder.';
COMMENT ON COLUMN lpg_batches.critical_alert_sent_at IS 'Set when the remaining=1 critical alert has been sent (dedupe).';

DROP TRIGGER IF EXISTS trg_lpg_batches_updated_at ON lpg_batches;
CREATE TRIGGER trg_lpg_batches_updated_at
    BEFORE UPDATE ON lpg_batches
    FOR EACH ROW EXECUTE FUNCTION lpg_touch_updated_at();

-- ---------------------------------------------------------------------------
-- lpg_generator_runs — one row per genset run (start -> stop)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lpg_generator_runs (
    id                 BIGSERIAL PRIMARY KEY,
    site_code          TEXT NOT NULL REFERENCES sites(code) ON DELETE RESTRICT,
    batch_id           BIGINT REFERENCES lpg_batches(id) ON DELETE SET NULL,
    generator_label    TEXT,                                 -- optional; for multi-genset sites
    status             TEXT NOT NULL DEFAULT 'running',      -- running | stopped
    started_at         TIMESTAMPTZ NOT NULL,
    start_soc_pct      NUMERIC(5, 2),
    start_reason       TEXT,
    start_operator     TEXT,
    start_instructor   TEXT,                                 -- CC user who logged the start
    ended_at           TIMESTAMPTZ,
    stop_soc_pct       NUMERIC(5, 2),
    stop_reason        TEXT,
    stop_operator      TEXT,
    stop_instructor    TEXT,
    lpg_depleted       BOOLEAN NOT NULL DEFAULT FALSE,
    cylinders_consumed INTEGER NOT NULL DEFAULT 0 CHECK (cylinders_consumed >= 0),
    runtime_seconds    INTEGER,
    created_by         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lpg_runs_site         ON lpg_generator_runs (site_code, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_lpg_runs_batch        ON lpg_generator_runs (batch_id);
CREATE INDEX IF NOT EXISTS idx_lpg_runs_open         ON lpg_generator_runs (site_code)
    WHERE status = 'running';

COMMENT ON TABLE  lpg_generator_runs                  IS 'Generator run log: start/stop with battery SOC, reasons, operators and LPG depletion.';
COMMENT ON COLUMN lpg_generator_runs.start_instructor IS 'CC portal user who logged the start (autofilled, editable).';
COMMENT ON COLUMN lpg_generator_runs.cylinders_consumed IS 'Cylinders emptied during this run (drives batch decrement). Usually 0 or 1.';

DROP TRIGGER IF EXISTS trg_lpg_runs_updated_at ON lpg_generator_runs;
CREATE TRIGGER trg_lpg_runs_updated_at
    BEFORE UPDATE ON lpg_generator_runs
    FOR EACH ROW EXECUTE FUNCTION lpg_touch_updated_at();
