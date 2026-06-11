-- Ring-fence internal treasury transfers in the merchant unmatched-payments queue.
--
-- The merchant exports include organisation-level money movements ("Transfer of funds
-- from M-Pesa Account", "Deposit into MMF Control Account", "Organisation Deposit of
-- Funds"). These are NOT customer payments: they must never be claimable onto a customer
-- account and must not pollute customer-payment datasets/totals. Audited 2026-06-11:
-- transactions table contains zero such rows; the fence is needed in this queue only.

BEGIN;

ALTER TABLE merchant_unmatched_payments
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'customer'
        CHECK (category IN ('customer', 'treasury'));

-- Classify existing parked rows.
UPDATE merchant_unmatched_payments
SET category = 'treasury'
WHERE category = 'customer'
  AND (
        reference_text ILIKE '%transfer of funds from m-pesa%'
     OR reference_text ILIKE '%control account%'
     OR reference_text ILIKE '%organisation deposit%'
     OR reference_text ILIKE '%organization deposit%'
     OR reference_text ILIKE '%deposit of funds%'
  );

-- Open-for-claim lookups only ever scan customer rows.
DROP INDEX IF EXISTS idx_merchant_unmatched_open;
CREATE INDEX idx_merchant_unmatched_open
    ON merchant_unmatched_payments (resolved_at)
    WHERE resolved_at IS NULL AND category = 'customer';

COMMENT ON COLUMN merchant_unmatched_payments.category IS
    'customer = potentially claimable customer payment; treasury = internal org transfer, ring-fenced (never claimable).';

COMMIT;
