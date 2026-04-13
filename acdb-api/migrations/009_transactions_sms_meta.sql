-- SMS ingestion audit: payer phone, remark snapshot, allocation method

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_payer_phone TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_remark_raw TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sms_allocation TEXT;

COMMENT ON COLUMN transactions.sms_payer_phone IS 'Payer MSISDN from M-Pesa SMS (normalized)';
COMMENT ON COLUMN transactions.sms_remark_raw IS 'Truncated Remark field from SMS for audit';
COMMENT ON COLUMN transactions.sms_allocation IS 'remark_account | phone_fallback — how account was chosen';
