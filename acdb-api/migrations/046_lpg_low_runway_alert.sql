-- 046_lpg_low_runway_alert.sql
--
-- Predictive "low runway" alerting for LPG. Building on 045_lpg_tracking, this
-- adds dedupe state for a proactive warn that fires when a site has fewer than
-- a threshold number of days of LPG left at its current burn rate (computed
-- from the trailing 30 days of generator-run consumption).
--
-- The alert is tied to the site's newest ACTIVE batch so it fires at most once
-- per stock period: when fresh LPG is delivered (a new batch), runway resets
-- and a new warn can fire later if consumption again outpaces supply. This is
-- distinct from the critical alert (045: remaining = last cylinder).
--
-- Deploy: auto-applied by CI against each country DB.

ALTER TABLE lpg_batches
    ADD COLUMN IF NOT EXISTS low_runway_alert_sent_at TIMESTAMPTZ;

COMMENT ON COLUMN lpg_batches.low_runway_alert_sent_at IS
    'Set when the predictive low-runway (days-left) warn alert has fired for the stock period covered by this (newest active) batch; NULL on new batches so runway warnings reset when LPG is delivered.';
