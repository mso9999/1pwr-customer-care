-- Migration 002: Add survey_id to accounts table
-- Establishes explicit 1:1 mapping between CC account_number and UGP Survey_ID
-- Replaces the implicit derivation convention (_survey_id_to_account_number)

BEGIN;

-- Add the column
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS survey_id VARCHAR(40);

-- Unique constraint: one account per UGP connection element
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_survey_id
    ON accounts (survey_id) WHERE survey_id IS NOT NULL;

-- Backfill from customers.plot_number for existing accounts.
-- Convention: plot_number is already in Survey_ID format ("MAK 0047 HH").
UPDATE accounts a
SET survey_id = c.plot_number
FROM customers c
WHERE a.customer_id = c.id
  AND a.survey_id IS NULL
  AND c.plot_number IS NOT NULL
  AND c.plot_number != ''
  AND TRIM(c.plot_number) != '';

COMMIT;
