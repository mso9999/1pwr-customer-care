-- 068: Unmetered service status + monthly service-fee ledger
--
-- Business rule (LS, 2026-08): a customer whose service is CONNECTED but who has
-- no meter yet is not on prepaid kWh — they owe a flat monthly service fee
-- (default 50 LSL, `system_config` key `unmetered_service_fee_amount`, editable
-- via /api/admin/country-fees). Until now there was no formal status for this:
-- the cohort page computes "connected, no meter" on the fly, but nothing
-- recorded who is in unmetered service, since when, or what they have been
-- charged. This migration adds that recordkeeping.
--
-- Model (mirrors account_advances / account_advance_ledger, migration 019):
--   unmetered_service         — one ACTIVE row per account (the enrollment);
--                               `outstanding` is the current unpaid service-fee
--                               debt, `monthly_fee` is a snapshot taken at
--                               enrollment so later tariff edits don't rewrite
--                               history.
--   unmetered_service_ledger  — append-only audit trail (accrual / repayment /
--                               adjustment). Monthly accrual is idempotent via
--                               the partial unique index on
--                               (service_id, accrual_period).
--
-- Entry is a manual staff action (POST /api/unmetered-service). Exit is
-- automatic: meter assignment (meter_lifecycle), commissioning
-- (commission/execute), or the accrual job's active-meter guard end the
-- enrollment; staff can also end it manually.
--
-- transactions gets service_fee_portion / unmetered_service_id so a top-up's
-- split (fee debt → service fee → advance → financing → electricity) is fully
-- recorded on the transaction row, same pattern as advance_portion/advance_id.

BEGIN;

CREATE TABLE IF NOT EXISTS unmetered_service (
    id                  SERIAL PRIMARY KEY,
    account_number      VARCHAR(32) NOT NULL,
    customer_id         INTEGER,
    status              VARCHAR(16) NOT NULL DEFAULT 'active',
    monthly_fee         NUMERIC(10,2) NOT NULL,
    repayment_fraction  NUMERIC(5,4) NOT NULL DEFAULT 0.5,
    outstanding         NUMERIC(10,2) NOT NULL DEFAULT 0,
    currency            VARCHAR(8) NOT NULL DEFAULT 'LSL',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMPTZ,
    end_reason          VARCHAR(32),
    note                TEXT,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One active enrollment per account.
CREATE UNIQUE INDEX IF NOT EXISTS uq_unmetered_service_active
    ON unmetered_service (account_number) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_unmetered_service_status
    ON unmetered_service (status);
CREATE INDEX IF NOT EXISTS idx_unmetered_service_customer
    ON unmetered_service (customer_id);

CREATE TABLE IF NOT EXISTS unmetered_service_ledger (
    id                      SERIAL PRIMARY KEY,
    service_id              INTEGER NOT NULL REFERENCES unmetered_service(id),
    entry_type              VARCHAR(24) NOT NULL,
    amount                  NUMERIC(10,2) NOT NULL,
    balance_after           NUMERIC(10,2) NOT NULL,
    accrual_period          VARCHAR(7),
    source_transaction_id   INTEGER,
    created_by              TEXT,
    note                    TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotent monthly accrual: one accrual row per enrollment per YYYY-MM.
CREATE UNIQUE INDEX IF NOT EXISTS uq_unmetered_ledger_accrual
    ON unmetered_service_ledger (service_id, accrual_period)
    WHERE entry_type = 'accrual';

CREATE INDEX IF NOT EXISTS idx_unmetered_ledger_service
    ON unmetered_service_ledger (service_id);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS service_fee_portion NUMERIC(10,2);
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS unmetered_service_id INTEGER;

COMMIT;
