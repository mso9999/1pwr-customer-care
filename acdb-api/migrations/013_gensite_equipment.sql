-- 013_gensite_equipment.sql
--
-- Generation site ("gensite") commissioning and telemetry schema.
--
-- Introduces:
--   sites                -- master list of physical sites (minigrids, health centers, ...)
--   site_equipment       -- installed generation hardware (inverters, BESS, meters, ...)
--   site_credentials     -- encrypted per-vendor backend credentials used to poll telemetry
--   inverter_readings    -- append-only time-series telemetry
--   inverter_alarms      -- event log with ack + O&M ticket linkage
--
-- Encryption: site_credentials.secret_ciphertext and api_key_ciphertext are
-- Fernet-encrypted at the application layer (see acdb-api/gensite/crypto.py).
-- Key lives in env var CC_CREDENTIAL_ENCRYPTION_KEY on the CC host, never in
-- the database. Rotation SOP: docs/ops/gensite-credentials.md.
--
-- Deploy: runs as postgres via CI (.github/workflows/deploy.yml) per the 010+
-- migration convention.

-- ---------------------------------------------------------------------------
-- sites
-- ---------------------------------------------------------------------------
-- Seeded at backend startup from country_config.site_abbrev for the active
-- country; PIH / non-minigrid sites are added via the commission wizard
-- (upsert) using whatever code UGP uses so the /gensite/{code} URL and the
-- UGP project key line up.

CREATE TABLE IF NOT EXISTS sites (
    code             TEXT PRIMARY KEY,
    country          TEXT NOT NULL,                 -- 'LS' / 'BN' / 'ZM'
    kind             TEXT NOT NULL DEFAULT 'minigrid',  -- 'minigrid' | 'health_center' | 'other'
    display_name     TEXT NOT NULL,
    district         TEXT,
    gps_lat          DOUBLE PRECISION,
    gps_lon          DOUBLE PRECISION,
    ugp_project_id   TEXT,                          -- uGridPLAN registry key (optional)
    commissioned_at  TIMESTAMPTZ,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sites_country ON sites (country);
CREATE INDEX IF NOT EXISTS idx_sites_kind    ON sites (kind);

COMMENT ON TABLE  sites                IS 'Master list of physical sites backing the gensite dashboard.';
COMMENT ON COLUMN sites.code           IS 'Canonical site code (matches country_config + UGP project key).';
COMMENT ON COLUMN sites.kind           IS 'minigrid | health_center | other.';
COMMENT ON COLUMN sites.ugp_project_id IS 'uGridPLAN registry key; nullable for sites not in UGP.';

-- ---------------------------------------------------------------------------
-- site_equipment
-- ---------------------------------------------------------------------------
-- One row per installed device. Multiple rows per site when a site has PV
-- inverter + BESS + metering. Vendor is recorded separately from the backend
-- (site_credentials.backend) because brand and cloud-portal are decoupled
-- (e.g. Deye and some rebrands share Solarman; SMA can talk Sunny Portal
-- or local Modbus).

CREATE TABLE IF NOT EXISTS site_equipment (
    id                  SERIAL PRIMARY KEY,
    site_code           TEXT NOT NULL REFERENCES sites(code) ON DELETE RESTRICT,
    kind                TEXT NOT NULL,              -- 'inverter' | 'bms' | 'pv_meter' | 'load_meter' | 'battery' | 'scada' | 'other'
    vendor              TEXT NOT NULL,              -- 'victron' | 'deye' | 'sinosoar' | 'sma' | 'other'
    model               TEXT,
    serial              TEXT,
    role                TEXT,                       -- 'grid_forming' | 'pv_input' | 'hybrid' | 'monitor' | 'storage'
    nameplate_kw        NUMERIC(10, 3),
    nameplate_kwh       NUMERIC(10, 3),
    firmware_version    TEXT,
    commissioned_at     TIMESTAMPTZ,
    decommissioned_at   TIMESTAMPTZ,
    installed_by        TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_site_equipment_site       ON site_equipment (site_code);
CREATE INDEX IF NOT EXISTS idx_site_equipment_site_kind  ON site_equipment (site_code, kind);
CREATE INDEX IF NOT EXISTS idx_site_equipment_active     ON site_equipment (site_code)
    WHERE decommissioned_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_site_equipment_serial
    ON site_equipment (site_code, vendor, serial)
    WHERE serial IS NOT NULL AND serial <> '';

COMMENT ON TABLE  site_equipment        IS 'Installed generation hardware per site.';
COMMENT ON COLUMN site_equipment.vendor IS 'Recorded brand; decoupled from telemetry backend.';

-- ---------------------------------------------------------------------------
-- site_credentials
-- ---------------------------------------------------------------------------
-- One row per (site, vendor, backend). Secrets stored as Fernet ciphertext
-- (bytea). API never returns plaintext; a masked view is exposed instead.

CREATE TABLE IF NOT EXISTS site_credentials (
    id                    SERIAL PRIMARY KEY,
    site_code             TEXT NOT NULL REFERENCES sites(code) ON DELETE RESTRICT,
    vendor                TEXT NOT NULL,            -- matches site_equipment.vendor
    backend               TEXT NOT NULL,            -- 'vrm' | 'solarman' | 'sunny_portal' | 'sinosoarcloud' | 'modbus_tcp' | 'other'
    base_url              TEXT,                     -- adapter default used if NULL
    username              TEXT,                     -- plaintext (display/masked only; not a secret)
    secret_ciphertext     BYTEA,                    -- Fernet-encrypted password
    api_key_ciphertext    BYTEA,                    -- Fernet-encrypted app key / bearer
    site_id_on_vendor     TEXT,                     -- e.g. Victron idSite, Solarman stationId, Sunny Portal plant id
    extra                 JSONB NOT NULL DEFAULT '{}'::JSONB,  -- adapter-specific knobs (appid for Solarman, etc.)
    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at            TIMESTAMPTZ,
    last_verified_at      TIMESTAMPTZ,
    last_verified_ok      BOOLEAN,
    last_verify_error     TEXT,
    CONSTRAINT uq_site_credentials_site_vendor_backend
        UNIQUE (site_code, vendor, backend)
);

CREATE INDEX IF NOT EXISTS idx_site_credentials_site   ON site_credentials (site_code);
CREATE INDEX IF NOT EXISTS idx_site_credentials_vendor ON site_credentials (vendor);

COMMENT ON TABLE  site_credentials                     IS 'Encrypted per-vendor backend credentials. Fernet ciphertext; key in CC_CREDENTIAL_ENCRYPTION_KEY env.';
COMMENT ON COLUMN site_credentials.secret_ciphertext   IS 'Fernet-encrypted password / portal secret.';
COMMENT ON COLUMN site_credentials.api_key_ciphertext  IS 'Fernet-encrypted API key / app secret (Solarman appSecret, VRM token, etc.).';
COMMENT ON COLUMN site_credentials.extra               IS 'Adapter-specific non-secret params (e.g. {"appid": "..."} for Solarman).';

-- ---------------------------------------------------------------------------
-- inverter_readings
-- ---------------------------------------------------------------------------
-- Append-only time-series. First pass: plain PG with a BRIN-friendly
-- (equipment_id, ts_utc) btree. Consider partitioning or Timescale once
-- fleet-wide polling steady-states.

CREATE TABLE IF NOT EXISTS inverter_readings (
    id                BIGSERIAL PRIMARY KEY,
    equipment_id      INTEGER NOT NULL REFERENCES site_equipment(id) ON DELETE CASCADE,
    site_code         TEXT NOT NULL,                -- denormalized for dashboard queries
    ts_utc            TIMESTAMPTZ NOT NULL,
    ac_kw             NUMERIC(10, 3),               -- instantaneous AC output
    ac_kwh_total      NUMERIC(14, 3),               -- lifetime AC energy counter
    dc_kw             NUMERIC(10, 3),
    pv_kw             NUMERIC(10, 3),
    battery_kw        NUMERIC(10, 3),               -- +charging / -discharging
    battery_soc_pct   NUMERIC(5, 2),
    grid_kw           NUMERIC(10, 3),               -- +import / -export
    ac_freq_hz        NUMERIC(6, 3),
    ac_v_avg          NUMERIC(6, 2),
    status_code       TEXT,                         -- vendor status string (normalized later)
    raw_json          JSONB                         -- full payload for post-hoc analysis
);

CREATE INDEX IF NOT EXISTS idx_inverter_readings_equipment_ts
    ON inverter_readings (equipment_id, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_inverter_readings_site_ts
    ON inverter_readings (site_code, ts_utc DESC);

COMMENT ON TABLE inverter_readings IS 'Append-only telemetry for gensite equipment. Dashboard queries latest-per-equipment.';

-- ---------------------------------------------------------------------------
-- inverter_alarms
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inverter_alarms (
    id                 BIGSERIAL PRIMARY KEY,
    equipment_id       INTEGER REFERENCES site_equipment(id) ON DELETE SET NULL,
    site_code          TEXT NOT NULL,
    vendor_code        TEXT,                         -- vendor's native alarm code
    vendor_msg         TEXT,
    severity           TEXT NOT NULL DEFAULT 'warning',  -- 'info' | 'warning' | 'critical'
    raised_at          TIMESTAMPTZ NOT NULL,
    cleared_at         TIMESTAMPTZ,
    acknowledged_by    TEXT,
    acknowledged_at    TIMESTAMPTZ,
    ticket_id_ugp      TEXT,                         -- O&M ticket ID in uGridPLAN
    event_json         JSONB,
    CONSTRAINT uq_inverter_alarms_vendor_event
        UNIQUE (equipment_id, vendor_code, raised_at)
);

CREATE INDEX IF NOT EXISTS idx_inverter_alarms_site_raised
    ON inverter_alarms (site_code, raised_at DESC);
CREATE INDEX IF NOT EXISTS idx_inverter_alarms_open
    ON inverter_alarms (site_code, severity)
    WHERE cleared_at IS NULL;

COMMENT ON TABLE inverter_alarms IS 'Alarm event log. Raised by the gensite poller; ack+ticket_id set via /api/gensite/sites/.../alarms/.../ack.';

-- ---------------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION gensite_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sites_updated_at          ON sites;
CREATE TRIGGER trg_sites_updated_at
    BEFORE UPDATE ON sites
    FOR EACH ROW EXECUTE FUNCTION gensite_touch_updated_at();

DROP TRIGGER IF EXISTS trg_site_equipment_updated_at ON site_equipment;
CREATE TRIGGER trg_site_equipment_updated_at
    BEFORE UPDATE ON site_equipment
    FOR EACH ROW EXECUTE FUNCTION gensite_touch_updated_at();
