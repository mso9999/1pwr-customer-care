-- Auditable operator confirmations for physical/site steps that CC cannot
-- determine from cloud telemetry. Automated provisioning evidence remains in
-- meter_provisioning, OTA, and validation tables.

CREATE TABLE IF NOT EXISTS onemeter_activation_steps (
    site_code       VARCHAR(16) NOT NULL,
    step_key        VARCHAR(64) NOT NULL,
    completed       BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_note   TEXT,
    completed_by    TEXT,
    completed_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (site_code, step_key)
);

CREATE INDEX IF NOT EXISTS idx_onemeter_activation_steps_status
    ON onemeter_activation_steps (site_code, completed, updated_at DESC);
