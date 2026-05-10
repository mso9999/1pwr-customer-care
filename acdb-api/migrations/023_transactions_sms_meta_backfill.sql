-- Ensure SMS audit columns exist on transactions.
-- Originally defined in 009_transactions_sms_meta.sql, which was a pre-010
-- "legacy ops" migration not auto-applied by the CI deploy script
-- (glob pattern 0[1-9][0-9]_*.sql skips 001-009).  This idempotent re-run
-- guarantees the columns are present on every environment.

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_payer_phone TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_remark_raw TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_allocation TEXT;

COMMENT ON COLUMN transactions.sms_payer_phone IS 'Payer MSISDN from M-Pesa SMS (normalized)';
COMMENT ON COLUMN transactions.sms_remark_raw IS 'Truncated Remark field from SMS for audit';
COMMENT ON COLUMN transactions.sms_allocation IS 'remark_account | phone_fallback — how account was chosen';
