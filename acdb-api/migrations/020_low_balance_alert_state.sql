-- 020: Low-balance SMS alerts driven by 1PDB balance_engine (not SparkMeter).
-- State machine on accounts + thresholds in system_config.
-- Job: scripts/ops/run_low_balance_alerts.py (systemd cc-low-balance-alerts.timer).

BEGIN;

ALTER TABLE accounts
  ADD COLUMN IF NOT EXISTS low_balance_alert_sent_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN accounts.low_balance_alert_sent_at IS
  'When set, low-balance SMS was sent for this account until balance clears above '
  'low_balance_kwh_clear (system_config). NULL means eligible to alert again.';

INSERT INTO system_config (key, value) VALUES
  ('low_balance_kwh_threshold', '10'),
  ('low_balance_kwh_clear', '20')
ON CONFLICT (key) DO NOTHING;

COMMIT;
