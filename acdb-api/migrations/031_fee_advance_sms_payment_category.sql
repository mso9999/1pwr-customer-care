-- Allow contract-only SMS gateway payments (fee debt + advance, no kWh).

BEGIN;

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_payment_category_check;

ALTER TABLE transactions
    ADD CONSTRAINT transactions_payment_category_check
    CHECK (payment_category IN (
        'electricity',
        'connection_fee',
        'readyboard_fee',
        'uncategorized',
        'fee_advance_sms'
    ));

COMMENT ON COLUMN transactions.payment_category IS
    'electricity / connection_fee / readyboard_fee / uncategorized / fee_advance_sms '
    '(contracts SMS mirror: fee debt + advance only, no SparkMeter kWh).';

COMMIT;
