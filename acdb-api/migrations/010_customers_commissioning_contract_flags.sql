-- Migration 010: Commissioning / contract completion flags on customers
--
-- Root cause addressed: commission/execute referenced columns that were not
-- guaranteed to exist in every deployment, causing UPDATE failures and aborted
-- transactions. These columns are part of the CC commissioning model and must
-- exist for pipeline + raw table views.

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS customer_commissioned BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS customer_commissioned_date DATE;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS contract_signed BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS contract_signed_date DATE;

COMMIT;
