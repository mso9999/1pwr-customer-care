-- 019_account_advances.sql
--
-- Connection / readyboard advances ledger.
--
-- Some customers cannot pay the up-front connection fee (501 LSL in Lesotho)
-- or the readyboard fee (499 LSL) at registration. 1PWR can advance the fee
-- and recover it from subsequent payments. This migration adds:
--
--   * ``account_advances``           — one open row per (account, advance_type),
--                                      currency-denominated outstanding balance
--                                      with a configurable repayment fraction
--                                      (default 0.50 = half of every payment
--                                      goes to the advance, half to kWh) and
--                                      an authorised monthly fee % accrual.
--   * ``account_advance_ledger``     — append-only audit trail (grant,
--                                      repayment, monthly_fee, adjustment,
--                                      writeoff).
--   * ``transactions`` columns       — ``payment_category`` (electricity /
--                                      connection_fee / readyboard_fee),
--                                      ``advance_portion`` and
--                                      ``electricity_portion`` for the split.
--
-- Both the contract upload metadata and the tamper-evidence sha256 are
-- ``NOT NULL`` so an advance literally cannot exist in the DB without a
-- signed contract on file.
--
-- See:
--   * acdb-api/advances.py              (router + helpers)
--   * acdb-api/fee_classifier.py        (amount → category resolver)
--   * scripts/ops/accrue_advance_fees.py (monthly accrual job)
--   * docs/ops/connection-readyboard-advances.md (operator runbook)
--
-- Idempotent: safe to re-apply.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'advance_type_enum') THEN
        CREATE TYPE advance_type_enum AS ENUM ('connection', 'readyboard');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'advance_status_enum') THEN
        CREATE TYPE advance_status_enum AS ENUM ('active', 'paid_off', 'written_off');
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- account_advances
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS account_advances (
    id                    BIGSERIAL PRIMARY KEY,
    account_number        TEXT NOT NULL,
    advance_type          advance_type_enum NOT NULL,
    original_amount       NUMERIC(12, 2) NOT NULL CHECK (original_amount > 0),
    outstanding           NUMERIC(12, 2) NOT NULL CHECK (outstanding >= 0),
    currency              TEXT NOT NULL,
    repayment_fraction    NUMERIC(5, 4) NOT NULL DEFAULT 0.5000
        CHECK (repayment_fraction >= 0 AND repayment_fraction <= 1),
    monthly_fee_pct       NUMERIC(7, 6) NOT NULL DEFAULT 0
        CHECK (monthly_fee_pct >= 0 AND monthly_fee_pct < 1),
    status                advance_status_enum NOT NULL DEFAULT 'active',
    created_by            TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accrual_at       TIMESTAMPTZ,
    paid_off_at           TIMESTAMPTZ,
    note                  TEXT,
    -- Contract metadata (mandatory at creation; uploaded PDF/image lives on disk).
    contract_path         TEXT NOT NULL,
    contract_filename     TEXT NOT NULL,
    contract_content_type TEXT NOT NULL,
    contract_size_bytes   BIGINT NOT NULL CHECK (contract_size_bytes > 0),
    contract_sha256       TEXT NOT NULL,
    contract_uploaded_by  TEXT NOT NULL,
    contract_uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One *active* advance per (account, type). Once paid_off / written_off another
-- one can be opened (rare, but should not be blocked).
CREATE UNIQUE INDEX IF NOT EXISTS ux_account_advances_active_per_type
    ON account_advances (account_number, advance_type)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_account_advances_account
    ON account_advances (account_number);

CREATE INDEX IF NOT EXISTS idx_account_advances_status_created
    ON account_advances (status, created_at DESC);

COMMENT ON TABLE  account_advances IS
    'Currency-denominated advances (connection / readyboard fees) granted to customers who cannot pay up-front. Repaid by automatic split of subsequent payments.';
COMMENT ON COLUMN account_advances.repayment_fraction IS
    'Fraction (0..1) of each non-fee payment that goes toward this advance. Default 0.50 = half to advance, half to kWh.';
COMMENT ON COLUMN account_advances.monthly_fee_pct IS
    'Monthly % fee assessed by the accrual job (e.g. 0.015 = 1.5% / month). Set per-advance by finance/onm/admin.';
COMMENT ON COLUMN account_advances.contract_path IS
    'Server-side filesystem path to the signed contract uploaded at advance creation. NOT NULL: every advance must have a contract on file.';
COMMENT ON COLUMN account_advances.contract_sha256 IS
    'SHA-256 of the uploaded contract file. Tamper-evidence; surfaced to finance for verification.';

-- ---------------------------------------------------------------------------
-- account_advance_ledger (append-only audit trail)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS account_advance_ledger (
    id                    BIGSERIAL PRIMARY KEY,
    advance_id            BIGINT NOT NULL REFERENCES account_advances(id) ON DELETE CASCADE,
    entry_type            TEXT NOT NULL CHECK (entry_type IN
                              ('grant', 'repayment', 'monthly_fee',
                               'adjustment', 'writeoff', 'contract_replaced')),
    amount                NUMERIC(12, 2) NOT NULL,
    balance_after         NUMERIC(12, 2) NOT NULL,
    source_transaction_id INTEGER,
    accrual_period        TEXT,                              -- 'YYYY-MM' for monthly_fee idempotency
    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_advance_ledger_advance_at
    ON account_advance_ledger (advance_id, created_at DESC);

-- One monthly_fee entry per (advance, calendar month) so the accrual job is
-- safe to re-run.
CREATE UNIQUE INDEX IF NOT EXISTS ux_advance_ledger_monthly_fee_period
    ON account_advance_ledger (advance_id, accrual_period)
    WHERE entry_type = 'monthly_fee' AND accrual_period IS NOT NULL;

COMMENT ON TABLE account_advance_ledger IS
    'Append-only ledger for account_advances. Entry types: grant, repayment, monthly_fee, adjustment, writeoff, contract_replaced.';

-- ---------------------------------------------------------------------------
-- transactions: payment_category + split portions for advances
-- ---------------------------------------------------------------------------
-- ``financing_portion`` / ``electricity_portion`` already exist (see financing.py).
-- We add a separate ``advance_portion`` so the financing and connection-advance
-- splits never overwrite each other, plus a coarse-grained category column
-- for reporting / dashboards.

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS payment_category TEXT
        CHECK (payment_category IN (
            'electricity', 'connection_fee', 'readyboard_fee', 'uncategorized'
        ));

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS advance_portion NUMERIC(12, 2);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS electricity_portion NUMERIC(12, 2);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS financing_portion NUMERIC(12, 2);

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS advance_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_transactions_payment_category
    ON transactions (payment_category)
    WHERE payment_category IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_advance_id
    ON transactions (advance_id)
    WHERE advance_id IS NOT NULL;

COMMENT ON COLUMN transactions.payment_category IS
    'Coarse routing decision made by fee_classifier: electricity (default), connection_fee or readyboard_fee (one-off fees that do not credit kWh).';
COMMENT ON COLUMN transactions.advance_portion IS
    'Currency portion of this payment applied to an account_advances row (see advance_id).';
COMMENT ON COLUMN transactions.advance_id IS
    'Foreign-key-style reference to account_advances.id when advance_portion > 0. Not enforced as FK to avoid blocking historical inserts.';

-- ---------------------------------------------------------------------------
-- system_config: country fee amounts (per active country backend)
-- ---------------------------------------------------------------------------
-- Each country API instance writes its own row. CountryConfig seeds defaults
-- in code; this table holds the authoritative live value that finance/onm/
-- superadmin can edit without a redeploy.

-- Defaults are written here so finance/onm/superadmin can edit them via
-- /api/admin/country-fees without a redeploy. Lesotho ships with the values
-- printed on contracts (501 / 499). Other country backends should overwrite
-- these on their own onepower_<cc> database during commissioning.
INSERT INTO system_config (key, value) VALUES
    ('connection_fee_amount', '501'),
    ('readyboard_fee_amount', '499')
ON CONFLICT (key) DO NOTHING;

COMMIT;
