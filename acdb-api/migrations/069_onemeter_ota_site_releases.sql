-- Self-serve per-site OTA release config (new-site onboarding).
-- Durable DB store that wins over the ota_releases.json file catalog (which the
-- deploy rsync resets). Non-secret only: WiFi passwords are never stored here;
-- they are entered in the provisioning station and live in device NVS.

CREATE TABLE IF NOT EXISTS onemeter_ota_site_releases (
    site_code               VARCHAR(16) PRIMARY KEY,
    artifact_key            TEXT NOT NULL,
    artifact_version_id     TEXT NOT NULL,
    target_firmware_version VARCHAR(32) NOT NULL,
    factory_baseline_version VARCHAR(32) NOT NULL DEFAULT '1.1.56',
    signing_profile         TEXT NOT NULL DEFAULT '1PWR_OTA_ESP32_v2',
    role_arn                TEXT NOT NULL,
    fallback_ssid           VARCHAR(64),
    canary_only             BOOLEAN NOT NULL DEFAULT TRUE,
    max_per_minute          INTEGER NOT NULL DEFAULT 1,
    created_by              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
