-- Migration 056: Financial metrics table (opex, EBITDA, CAPEX)
-- Stores financial metrics from Odoo / financial model workbook.

BEGIN;

CREATE TABLE IF NOT EXISTS financial_metrics (
    id SERIAL PRIMARY KEY,
    site_code VARCHAR(3),
    period VARCHAR(7) NOT NULL,
    opex_usd NUMERIC(14,2),
    ebitda_usd NUMERIC(14,2),
    capex_deployed_usd NUMERIC(14,2),
    capex_cumulative_usd NUMERIC(14,2),
    source VARCHAR(50) DEFAULT 'odoo',
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (site_code, period, source)
);

CREATE INDEX IF NOT EXISTS idx_financial_metrics_period
    ON financial_metrics (period);

COMMIT;
