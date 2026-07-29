-- Enforce the durable gateway -> meter -> customer handoff used by 1Meter.
--
-- A customer may temporarily have more than one meter (for example a
-- SparkMeter primary plus a 1Meter check meter), so account_number is not
-- globally unique. A physical meter serial, however, may only have one active
-- lifecycle assignment and one commissioned gateway.

ALTER TABLE meter_provisioning
    ADD COLUMN IF NOT EXISTS commissioned_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_meter_assignments_active_meter
    ON meter_assignments (meter_id)
    WHERE removed_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_meter_provisioning_commissioned_serial
    ON meter_provisioning ((regexp_replace(meter_serial, '^0+', '')))
    WHERE meter_serial IS NOT NULL
      AND meter_serial <> ''
      AND account_number IS NOT NULL
      AND account_number <> ''
      AND status = 'commissioned';
