-- kWh used after prepaid credit hit zero and before the relay opened.
-- The balance engine already carries this as a negative (payments − consumption).
-- This column and ledger are that overshoot, so it is visible as customer debt.
-- A later electricity purchase fills the hole in the balance; it is not charged again.

BEGIN;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS energy_debt_kwh NUMERIC(14, 4) NOT NULL DEFAULT 0;

COMMENT ON COLUMN customers.energy_debt_kwh IS
    'kWh used beyond prepaid credit (cutoff lag). Paid down when later purchases lift the balance back through zero. Not a second charge.';

CREATE TABLE IF NOT EXISTS energy_debt_ledger (
    id                SERIAL PRIMARY KEY,
    account_number    VARCHAR(32) NOT NULL,
    customer_id       INTEGER,
    entry_type        VARCHAR(16) NOT NULL,
    kwh               NUMERIC(14, 4) NOT NULL,
    debt_after_kwh    NUMERIC(14, 4) NOT NULL,
    tariff_rate       NUMERIC(14, 4),
    currency_amount   NUMERIC(14, 2),
    source            VARCHAR(32),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT energy_debt_ledger_entry_type
        CHECK (entry_type IN ('accrual', 'repayment'))
);

CREATE INDEX IF NOT EXISTS idx_energy_debt_ledger_account
    ON energy_debt_ledger (account_number, id DESC);

COMMIT;
