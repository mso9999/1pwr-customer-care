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
-- Duplicate-safe: a plot_number (connection) can be shared by multiple accounts
-- in the legacy data (e.g. "SHG 0310 SME" x5). The unique index allows only one
-- account per survey_id, so assign it to the first account (lowest id) per
-- plot_number; the rest stay NULL for manual disambiguation.
UPDATE accounts a
SET survey_id = c.plot_number
FROM customers c
WHERE a.customer_id = c.id
  AND a.survey_id IS NULL
  AND c.plot_number IS NOT NULL
  AND TRIM(c.plot_number) != ''
  AND a.id = (
    SELECT MIN(a2.id)
    FROM accounts a2
    JOIN customers c2 ON a2.customer_id = c2.id
    WHERE c2.plot_number = c.plot_number
  );

COMMIT;
