-- Manual decisions for positive contract/debt credit:
--   - convert to electricity (creates kWh credit transaction)
--   - refund (cash handled off-platform; decision logged in DB)
--
-- This table is append-only and links every decision back to the source
-- sms_gateway_contract transaction row so available credit is deterministic.

BEGIN;

CREATE TABLE IF NOT EXISTS financial_credit_decisions (
    id BIGSERIAL PRIMARY KEY,
    account_number TEXT NOT NULL,
    source_transaction_id BIGINT NOT NULL,
    decision_type TEXT NOT NULL CHECK (decision_type IN ('convert', 'refund')),
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    related_transaction_id BIGINT,
    note TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fin_credit_decisions_account_created
    ON financial_credit_decisions (account_number, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fin_credit_decisions_source
    ON financial_credit_decisions (source_transaction_id);

COMMENT ON TABLE financial_credit_decisions IS
    'Append-only manual decisions for positive financial/contract credit: convert to electricity or refund.';

COMMENT ON COLUMN financial_credit_decisions.source_transaction_id IS
    'transactions.id for the originating sms_gateway_contract payment row that carried the positive credit.';

COMMENT ON COLUMN financial_credit_decisions.related_transaction_id IS
    'For decision_type=convert, points to the synthetic electricity credit transaction id.';

COMMIT;
