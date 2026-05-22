-- Ensure transaction_source enum supports contract-channel and manual
-- debt-credit conversion sources used by ingest/advances workflows.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'transaction_source') THEN
        ALTER TYPE transaction_source ADD VALUE IF NOT EXISTS 'sms_gateway_contract';
        ALTER TYPE transaction_source ADD VALUE IF NOT EXISTS 'manual_contract_credit_convert';
    END IF;
END$$;

COMMIT;
