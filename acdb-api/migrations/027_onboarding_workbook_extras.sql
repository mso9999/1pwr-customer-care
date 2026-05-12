-- Extra onboarding fields from the Customer Onboarding workbook (house wiring, CIU, external proof URLs).

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS house_wiring_test_passed BOOLEAN,
    ADD COLUMN IF NOT EXISTS house_wiring_test_date DATE,
    ADD COLUMN IF NOT EXISTS ciu_payment_date DATE,
    ADD COLUMN IF NOT EXISTS voltage_test_passed BOOLEAN,
    ADD COLUMN IF NOT EXISTS voltage_test_date DATE,
    ADD COLUMN IF NOT EXISTS meter_autostate_test_passed BOOLEAN,
    ADD COLUMN IF NOT EXISTS meter_autostate_test_date DATE,
    ADD COLUMN IF NOT EXISTS onboarding_import_tag VARCHAR(64);

ALTER TABLE payment_proofs
    ADD COLUMN IF NOT EXISTS external_url TEXT;

COMMIT;
