-- First-canary self-service and evidence-gated OTA release approval.

ALTER TABLE meter_provisioning
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_meter_provisioning_test_units
    ON meter_provisioning (site, is_test)
    WHERE is_test = TRUE;

CREATE TABLE IF NOT EXISTS onemeter_ota_release_approvals (
    id                      BIGSERIAL PRIMARY KEY,
    site_code               VARCHAR(16) NOT NULL,
    artifact_version_id     TEXT NOT NULL,
    target_firmware_version VARCHAR(32) NOT NULL,
    canary_ota_update_id    VARCHAR(64) NOT NULL,
    validation_session_id   UUID,
    validation_waived       BOOLEAN NOT NULL DEFAULT FALSE,
    waiver_reason           TEXT,
    approved_by             TEXT NOT NULL,
    approved_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at              TIMESTAMPTZ,
    revoked_by              TEXT,
    UNIQUE (site_code, artifact_version_id, target_firmware_version)
);

CREATE INDEX IF NOT EXISTS idx_onemeter_ota_release_approvals_active
    ON onemeter_ota_release_approvals
        (site_code, artifact_version_id, target_firmware_version)
    WHERE revoked_at IS NULL;
