-- Proactive balance freshness: per-account live SparkMeter balance cache.
--
-- Stores the meter's near-real-time credit (Koios v1 / ThunderCloud v0) so balance
-- reads (SMS gateway, portal dashboard, low-balance alerts) can show a value fresher
-- than the ~1-day readings batch. `live_balance_kwh` is the display value; the raw
-- SM reading and the contemporaneous balance_engine value are kept for drift audit.

BEGIN;

CREATE TABLE IF NOT EXISTS account_balance_live (
    account_number       TEXT PRIMARY KEY,
    live_balance_kwh     DOUBLE PRECISION,            -- display value (= SM meter balance, kWh)
    sm_balance_kwh       DOUBLE PRECISION,            -- raw SM meter balance from last good lookup
    sm_balance_currency  DOUBLE PRECISION,            -- raw SM credit in currency (Koios) for audit
    cc_balance_kwh       DOUBLE PRECISION,            -- balance_engine value at pull time (drift)
    source               TEXT NOT NULL DEFAULT 'engine',  -- koios | thundercloud | engine
    as_of                TIMESTAMPTZ,                 -- time of the SM reading (= pull time)
    stale                BOOLEAN NOT NULL DEFAULT FALSE,
    last_error           TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE account_balance_live IS
    'Cache of near-real-time SparkMeter meter balances for balance-display freshness (see balance_live.py).';
COMMENT ON COLUMN account_balance_live.live_balance_kwh IS
    'Value shown to callers: the SM meter balance in kWh (the credit the meter will honour).';
COMMENT ON COLUMN account_balance_live.cc_balance_kwh IS
    'balance_engine.get_balance_kwh at pull time; live minus this = import-lag/credit drift.';
COMMENT ON COLUMN account_balance_live.stale IS
    'True when serving a cached value because the latest live lookup failed (meter offline/unreachable).';

-- Live-cache TTL: within this many seconds an activity-triggered read reuses the
-- cached value instead of calling SparkMeter (dedupes activity vs scheduled pulls).
INSERT INTO system_config (key, value)
SELECT 'balance_live_ttl_s', '600'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_live_ttl_s');

COMMIT;
