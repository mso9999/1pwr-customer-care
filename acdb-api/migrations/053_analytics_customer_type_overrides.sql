-- Migration 053: Customer type overrides table
-- Audit trail for manual customer type reclassification (HH/SME/C_I/UNK).
-- Checked first by the classifier before falling back to tariff-plan-based rules.

BEGIN;

CREATE TABLE IF NOT EXISTS customer_type_overrides (
    id SERIAL PRIMARY KEY,
    account_number VARCHAR(50) NOT NULL UNIQUE,
    customer_type VARCHAR(10) NOT NULL,
    reason VARCHAR(255),
    overridden_by VARCHAR(100),
    overridden_at TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;
