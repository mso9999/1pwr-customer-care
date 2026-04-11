-- Optional external payment reference (M-Pesa receipt, EcoCash ref, etc.)
-- Used for deduplication on manual portal credits and SMS gateway webhooks.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_reference TEXT;

COMMENT ON COLUMN transactions.payment_reference IS 'External provider receipt/ref; unique when set (case-insensitive trim)';

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_payment_reference_unique
ON transactions (lower(trim(payment_reference)))
WHERE payment_reference IS NOT NULL AND trim(payment_reference) <> '';
