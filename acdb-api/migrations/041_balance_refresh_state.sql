-- Proactive balance freshness: per-account consumption rate + tiered pull schedule.
--
-- `recompute_consumption_rate.py` writes `avg_kwh_per_hour`; `balance_refresh_scheduler.py`
-- estimates time-to-depletion (balance / rate, predicted forward between pulls), assigns a
-- cadence tier, and pulls the live SparkMeter balance for accounts nearing zero.

BEGIN;

CREATE TABLE IF NOT EXISTS balance_refresh_state (
    account_number      TEXT PRIMARY KEY,
    avg_kwh_per_hour    DOUBLE PRECISION NOT NULL DEFAULT 0,  -- blended recent consumption rate
    last_balance_kwh    DOUBLE PRECISION,                     -- freshest known balance (live cache / pull)
    last_balance_at     TIMESTAMPTZ,                          -- when last_balance_kwh was observed
    hours_to_depletion  DOUBLE PRECISION,                     -- predicted hours until balance hits 0
    tier                SMALLINT NOT NULL DEFAULT 0,          -- 0 = no scheduled pull; 1..4 escalating
    last_pull_at        TIMESTAMPTZ,                          -- last scheduled live pull
    next_due_at         TIMESTAMPTZ,                          -- when this account is next due for a pull
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_balance_refresh_due
    ON balance_refresh_state (next_due_at)
    WHERE next_due_at IS NOT NULL;

COMMENT ON TABLE balance_refresh_state IS
    'Per-account consumption rate + tiered live-balance pull schedule (see balance_refresh_scheduler.py).';
COMMENT ON COLUMN balance_refresh_state.tier IS
    '0=no scheduled pull (>24h or idle/depleted); 1=2h (12-24h left); 2=1h (6-12h); 3=15m (1-6h); 4=5m (<=1h).';

-- Rate-blend weights/windows (Part 4): rate = w*(kWh_recent/recent_h) + (1-w)*(kWh_window/window_h).
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_rate_w_recent', '0.6'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_rate_w_recent');
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_rate_recent_hours', '48'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_rate_recent_hours');
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_rate_window_hours', '168'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_rate_window_hours');

-- Tier boundaries (hours-to-depletion, descending) and matching cadences (minutes).
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_tier_hours', '24,12,6,1'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_tier_hours');
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_tier_cadence_min', '120,60,15,5'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_tier_cadence_min');

-- Rate-limit budget (Koios is ~30k/day shared with imports): per scheduler run + per local day.
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_max_per_run', '400'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_max_per_run');
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_daily_budget', '8000'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_daily_budget');
INSERT INTO system_config (key, value)
SELECT 'balance_refresh_bootstrap_per_run', '100'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'balance_refresh_bootstrap_per_run');

COMMIT;
