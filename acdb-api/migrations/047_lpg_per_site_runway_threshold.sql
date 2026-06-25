-- 047_lpg_per_site_runway_threshold.sql
--
-- Per-site override for the predictive low-runway warn threshold (days of LPG
-- left at current burn rate). NULL means "use the module default"
-- (lpg.store.LOW_RUNWAY_WARN_DAYS = 7). A remote site with long resupply lead
-- times can set a larger value so it is warned earlier.
--
-- Lives on sites (alongside the gensite flow_balance_* settings, migration 039)
-- so it is shared by the synchronous run-stop alert and the daily sweep.
--
-- Deploy: auto-applied by CI against each country DB.

ALTER TABLE sites
    ADD COLUMN IF NOT EXISTS lpg_low_runway_warn_days INTEGER;

COMMENT ON COLUMN sites.lpg_low_runway_warn_days IS
    'Per-site LPG low-runway warn threshold in days; NULL = module default (7). Warn fires when projected days of LPG left at current burn rate fall below this.';
