-- Meter detail and the check-meter report read prototype_meter_state.last_sample_time.
-- Lesotho's prototype sync created it ad hoc; Benin never had it, so every Benin
-- meter page returned HTTP 500 (2026-09-27).

ALTER TABLE prototype_meter_state
  ADD COLUMN IF NOT EXISTS last_sample_time VARCHAR(20);

COMMENT ON COLUMN prototype_meter_state.last_sample_time IS
  'sample_time (YYYYMMDDHHMM) of the newest 1meter_data row synced for this meter.';
