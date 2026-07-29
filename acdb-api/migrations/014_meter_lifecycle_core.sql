-- Canonicalize the lifecycle tables that were historically created by API
-- startup helpers. New country databases must have them before migrations 044
-- and 057 add structural invariants.

CREATE TABLE IF NOT EXISTS meter_assignments (
    id              SERIAL PRIMARY KEY,
    meter_id        VARCHAR(80) NOT NULL,
    account_number  VARCHAR(20) NOT NULL,
    community       VARCHAR(10),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at      TIMESTAMPTZ,
    removal_reason  TEXT,
    replaced_by     VARCHAR(80),
    notes           TEXT,
    created_by      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ma_meter
    ON meter_assignments (meter_id);
CREATE INDEX IF NOT EXISTS idx_ma_account
    ON meter_assignments (account_number);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ma_active
    ON meter_assignments (meter_id, account_number)
    WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS meter_provisioning (
    id                      SERIAL PRIMARY KEY,
    thing_name              VARCHAR(128) NOT NULL UNIQUE,
    meter_serial            VARCHAR(64),
    pcb_mac                 VARCHAR(32),
    site                    VARCHAR(16),
    account_number          VARCHAR(32),
    cert_id                 VARCHAR(128),
    cert_arn                TEXT,
    status                  VARCHAR(24) NOT NULL DEFAULT 'provisioned',
    legacy_id               VARCHAR(128),
    fw_version              VARCHAR(32),
    provisioned_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provisioned_by          TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    box_label               VARCHAR(64),
    first_seen_online       TIMESTAMPTZ,
    last_seen_online        TIMESTAMPTZ,
    ota_update_id           VARCHAR(64),
    ota_target_version      VARCHAR(32),
    ota_status              VARCHAR(24),
    ota_updated_at          TIMESTAMPTZ,
    deployment_wifi_ssid    VARCHAR(64),
    wifi_config_version     INTEGER,
    wifi_configured_at      TIMESTAMPTZ,
    commissioned_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mp_serial
    ON meter_provisioning (meter_serial);
CREATE INDEX IF NOT EXISTS idx_mp_site
    ON meter_provisioning (site);
CREATE INDEX IF NOT EXISTS idx_mp_account
    ON meter_provisioning (account_number);

CREATE TABLE IF NOT EXISTS gateway_pool_seq (
    site        VARCHAR(16) PRIMARY KEY,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
