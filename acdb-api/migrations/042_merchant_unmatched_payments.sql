-- Holding queue for merchant-line payments whose reference matched no CC account.
--
-- RCA 2026-06-11 (0231MAK): M-Pesa merchant payments only enter CC via the operator-run
-- merchant-export backfill; payments whose Reference resolved to no account were silently
-- dropped. This table parks them so they can be claimed automatically when the account is
-- later registered (see merchant_unmatched.claim_unmatched_for_account).

BEGIN;

CREATE TABLE IF NOT EXISTS merchant_unmatched_payments (
    id              BIGSERIAL PRIMARY KEY,
    receipt         TEXT NOT NULL,                 -- provider receipt (external id)
    amount          NUMERIC(12,2) NOT NULL,
    paid_at         TIMESTAMPTZ NOT NULL,
    reference_text  TEXT NOT NULL DEFAULT '',      -- raw Reference/Remark from the export
    payer_phone     TEXT NOT NULL DEFAULT '',
    site_hint       TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    source_file     TEXT NOT NULL DEFAULT '',
    parked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolved_txn_id BIGINT,
    resolved_account TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_merchant_unmatched_receipt
    ON merchant_unmatched_payments (lower(receipt))
    WHERE receipt <> '';

CREATE INDEX IF NOT EXISTS idx_merchant_unmatched_open
    ON merchant_unmatched_payments (resolved_at) WHERE resolved_at IS NULL;

COMMENT ON TABLE merchant_unmatched_payments IS
    'Merchant-export payments with unresolvable account references, parked for later claim (see merchant_unmatched.py).';

COMMIT;
