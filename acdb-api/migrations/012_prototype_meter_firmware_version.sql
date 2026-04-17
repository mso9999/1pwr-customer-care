-- Optional firmware version string reported by 1Meter via POST /api/meters/reading
-- (ingestion_gate Lambda must forward MQTT field after firmware publishes it).

ALTER TABLE prototype_meter_state
  ADD COLUMN IF NOT EXISTS firmware_version TEXT;

COMMENT ON COLUMN prototype_meter_state.firmware_version IS
  'App/firmware version reported by device (e.g. OTA semver). Updated when payload includes firmware_version.';
