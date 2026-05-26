-- 039_gensite_flow_balance_settings.sql
--
-- Per-site tuning knobs for power-flow balance deviation alerts in the gensite UI.
-- Defaults remain in frontend/backend code when columns are NULL.

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS flow_balance_warn_pct NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS flow_balance_crit_pct NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS flow_balance_min_scale_kw NUMERIC(8, 3);

COMMENT ON COLUMN sites.flow_balance_warn_pct
    IS 'Warn threshold (%) for |PV + genset + battery(in-/out+) - load| / scale in gensite powerflow.';
COMMENT ON COLUMN sites.flow_balance_crit_pct
    IS 'Critical threshold (%) for powerflow balance deviation. Should be >= warn threshold.';
COMMENT ON COLUMN sites.flow_balance_min_scale_kw
    IS 'Minimum denominator scale (kW) for deviation percentage to avoid low-load blowups.';
