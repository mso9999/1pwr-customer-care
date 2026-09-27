-- Asset-financing tables read by financing.py and financing_penalties.py.
-- Lesotho (onepower_cc) got these ad hoc; Benin and Zambia never had them, so the
-- customer page's financing panel returned HTTP 500 there (2026-09-27).
-- Matches the onepower_cc definitions; no-op where the tables already exist.

CREATE TABLE IF NOT EXISTS financing_products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    default_principal NUMERIC(12,2) NOT NULL DEFAULT 0,
    default_interest_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
    default_setup_fee NUMERIC(12,2) NOT NULL DEFAULT 0,
    default_repayment_fraction NUMERIC(5,4) NOT NULL DEFAULT 0.20,
    default_penalty_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
    default_penalty_grace_days INTEGER NOT NULL DEFAULT 30,
    default_penalty_interval_days INTEGER NOT NULL DEFAULT 30,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS financing_agreements (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER,
    account_number TEXT NOT NULL,
    product_id INTEGER REFERENCES financing_products(id),
    description TEXT NOT NULL,
    principal NUMERIC(12,2) NOT NULL,
    interest_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    setup_fee NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_owed NUMERIC(12,2) NOT NULL,
    outstanding_balance NUMERIC(12,2) NOT NULL,
    repayment_fraction NUMERIC(5,4) NOT NULL DEFAULT 0.20,
    penalty_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
    penalty_grace_days INTEGER NOT NULL DEFAULT 30,
    penalty_interval_days INTEGER NOT NULL DEFAULT 30,
    contract_path TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CONSTRAINT financing_agreements_status_check
        CHECK (status IN ('active', 'paid_off', 'defaulted', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    paid_off_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS financing_ledger (
    id SERIAL PRIMARY KEY,
    agreement_id INTEGER NOT NULL REFERENCES financing_agreements(id),
    entry_type TEXT NOT NULL
        CONSTRAINT financing_ledger_entry_type_check
        CHECK (entry_type IN ('payment', 'penalty', 'adjustment', 'fee', 'writeoff')),
    amount NUMERIC(12,2) NOT NULL,
    balance_after NUMERIC(12,2) NOT NULL,
    source_transaction_id INTEGER,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_fin_agreements_account ON financing_agreements (account_number);
CREATE INDEX IF NOT EXISTS idx_fin_agreements_status ON financing_agreements (status);
CREATE INDEX IF NOT EXISTS idx_fin_ledger_agreement ON financing_ledger (agreement_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON financing_products, financing_agreements, financing_ledger TO cc_api;
GRANT USAGE, SELECT ON SEQUENCE financing_products_id_seq, financing_agreements_id_seq, financing_ledger_id_seq TO cc_api;
