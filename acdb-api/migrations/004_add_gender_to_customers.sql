-- Migration 004: Add optional gender to customers
-- Stores customer demographic data on the customer entity, not accounts.

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS gender VARCHAR(16);

COMMIT;
