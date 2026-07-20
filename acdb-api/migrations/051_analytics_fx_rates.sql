-- Migration 051: FX rates table with effective dates
-- Replaces hardcoded _FX_TO_USD constants in stats.py.
-- Enables accurate period-appropriate USD conversion for investor reporting.

BEGIN;

CREATE TABLE IF NOT EXISTS fx_rates (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(3) NOT NULL,
    rate_to_usd NUMERIC(10,6) NOT NULL,
    effective_date DATE NOT NULL,
    source VARCHAR(50) DEFAULT 'manual',
    UNIQUE (currency, effective_date)
);

-- Seed approximate historical rates (indicative, not live market rates)
INSERT INTO fx_rates (currency, rate_to_usd, effective_date, source)
VALUES
    ('LSL', 0.054, '2024-01-01', 'manual'),
    ('LSL', 0.054, '2025-01-01', 'manual'),
    ('LSL', 0.057, '2026-01-01', 'manual'),
    ('XOF', 0.0016, '2024-01-01', 'manual'),
    ('XOF', 0.0016, '2025-01-01', 'manual'),
    ('XOF', 0.0016, '2026-01-01', 'manual'),
    ('ZMW', 0.039, '2024-01-01', 'manual'),
    ('ZMW', 0.036, '2025-01-01', 'manual'),
    ('ZMW', 0.036, '2026-01-01', 'manual')
ON CONFLICT (currency, effective_date) DO NOTHING;

COMMIT;
