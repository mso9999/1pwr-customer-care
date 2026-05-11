-- 025_payment_status_overrides.sql
-- Manual payment status overrides for ops team + proof-of-payment uploads.

-- 1. Add override column to customers
ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS payment_status_override VARCHAR(16);

ALTER TABLE customers
  DROP CONSTRAINT IF EXISTS chk_payment_status_override;

ALTER TABLE customers
  ADD CONSTRAINT chk_payment_status_override
  CHECK (payment_status_override IS NULL
         OR payment_status_override IN ('not_paid', 'paid', 'fully_paid'));

-- 2. Track who set it and when
ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS payment_status_override_by TEXT;
ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS payment_status_override_at TIMESTAMPTZ;

-- 3. Proof-of-payment uploads table
CREATE TABLE IF NOT EXISTS payment_proofs (
    id             BIGSERIAL PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(id),
    file_path      TEXT NOT NULL,
    file_name      TEXT NOT NULL,
    content_type   TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    uploaded_by    TEXT,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note           TEXT
);

CREATE INDEX IF NOT EXISTS idx_payment_proofs_customer
    ON payment_proofs(customer_id);
