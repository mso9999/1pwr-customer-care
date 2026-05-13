-- Fee debt (currency owed toward connection / readyboard) per customer.
-- Parallel to fee_classifier + payment_verifications; debt is reduced by
-- exact fee SMS rows and by the electricity-path 50% cap allocator.

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS fee_debt_connection_remaining NUMERIC(14, 2) NOT NULL DEFAULT 0;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS fee_debt_readyboard_remaining NUMERIC(14, 2) NOT NULL DEFAULT 0;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS acquires_1pwr_readyboard BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN customers.fee_debt_connection_remaining IS
    'Currency still owed toward the country connection fee before kWh-only classification.';

COMMENT ON COLUMN customers.fee_debt_readyboard_remaining IS
    'Currency still owed toward the country readyboard fee when acquires_1pwr_readyboard.';

COMMENT ON COLUMN customers.acquires_1pwr_readyboard IS
    'If true at registration, readyboard fee debt was seeded from country fees.';

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS fee_repayment_portion NUMERIC(14, 2);

COMMENT ON COLUMN transactions.fee_repayment_portion IS
    'Portion of this payment applied to connection+readyboard fee debt (electricity-classified path).';

COMMIT;
