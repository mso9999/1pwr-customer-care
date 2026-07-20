-- Migration 054: Invoiced revenue table (Odoo / Benin)
-- Stores invoiced (non-prepaid) revenue from Odoo for clinic/institutional customers.

BEGIN;

CREATE TABLE IF NOT EXISTS invoiced_revenue (
    id SERIAL PRIMARY KEY,
    invoice_id VARCHAR(255) NOT NULL,
    account_number VARCHAR(50),
    site_code VARCHAR(3),
    customer_name VARCHAR(255),
    customer_type VARCHAR(10) DEFAULT 'C_I',
    invoice_date DATE NOT NULL,
    period VARCHAR(7) NOT NULL,
    kwh NUMERIC(10,2),
    amount_local NUMERIC(12,2),
    currency VARCHAR(3) DEFAULT 'XOF',
    amount_usd NUMERIC(12,2),
    collection_status VARCHAR(20) DEFAULT 'invoiced',
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (invoice_id)
);

CREATE INDEX IF NOT EXISTS idx_invoiced_revenue_site_period
    ON invoiced_revenue (site_code, period);
CREATE INDEX IF NOT EXISTS idx_invoiced_revenue_period
    ON invoiced_revenue (period);

COMMIT;
