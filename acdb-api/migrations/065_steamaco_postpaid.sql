-- Migration 065: Steamaco clinic-meter integration
--
-- 1. Add 'steamaco' as a valid transaction_source (hourly_consumption.source)
--    for the Steamaco Savi 3-phase clinic meters (BOB/KET/LEB/MAN/MET/NKU/TLH),
--    imported by scripts/ops/import_steamaco.py from the Steamaco Nimbus API.
-- 2. Add accounts.billing_model — 'prepaid' (default, existing behaviour) or
--    'postpaid' (institutional invoice customers like the PIH clinics).
--    Postpaid accounts are excluded from low-balance SMS alerts and the
--    SM balance audit, and their CC balance reads as "unbilled kWh owed".

ALTER TYPE transaction_source ADD VALUE IF NOT EXISTS 'steamaco';

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS billing_model VARCHAR(16) NOT NULL DEFAULT 'prepaid';

ALTER TABLE accounts
  DROP CONSTRAINT IF EXISTS chk_billing_model;
ALTER TABLE accounts
    ADD CONSTRAINT chk_billing_model
    CHECK (billing_model IN ('prepaid', 'postpaid'));

COMMENT ON COLUMN accounts.billing_model IS
    'prepaid (default; PAYG kWh credit) or postpaid (institutional, invoiced monthly — e.g. PIH clinics on Steamaco meters).';

-- Postpaid clinic accounts are billed from Steamaco telemetry.
-- billing_meter_priority='steamaco' is set per-account by the registry script;
-- balance_engine accepts it as a valid priority (see VALID_PRIORITIES).
