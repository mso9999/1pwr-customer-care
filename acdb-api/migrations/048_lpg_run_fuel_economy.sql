-- 048_lpg_run_fuel_economy.sql
--
-- Add total_kwh to lpg_generator_runs so fuel economy (kg/kWh, kg/hr)
-- can be persisted at stop time and surfaced in the runs table without
-- needing a live join to gensite_hourly_metrics on every list query.

ALTER TABLE lpg_generator_runs
    ADD COLUMN IF NOT EXISTS total_kwh NUMERIC(10, 3);

COMMENT ON COLUMN lpg_generator_runs.total_kwh IS
'Total kWh generated during the run, summed from gensite_hourly_metrics.avg_genset_kw over the run window. NULL for runs that predate this column or where no telemetry is available.';
