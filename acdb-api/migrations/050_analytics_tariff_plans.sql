-- Migration 050: SparkMeter tariff plans table
-- Stores tariff plans per site pulled from SparkMeter API, with customer type classification.
-- Used by investor analytics KPI endpoints to compute HH/SME/C&I splits and avg tariff.

BEGIN;

CREATE TABLE IF NOT EXISTS sm_tariff_plans (
    id SERIAL PRIMARY KEY,
    site_code VARCHAR(3) NOT NULL,
    plan_id VARCHAR(255) NOT NULL,
    plan_name VARCHAR(255) NOT NULL,
    rate_amount NUMERIC(10,4),
    currency VARCHAR(3) DEFAULT 'LSL',
    customer_type VARCHAR(10) DEFAULT 'UNK',
    is_business BOOLEAN DEFAULT FALSE,
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (site_code, plan_id)
);

CREATE INDEX IF NOT EXISTS idx_sm_tariff_plans_site
    ON sm_tariff_plans (site_code);

COMMIT;
