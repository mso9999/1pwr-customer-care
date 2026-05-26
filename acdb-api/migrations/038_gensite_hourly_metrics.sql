-- 038_gensite_hourly_metrics.sql
--
-- Hourly archival rollups for gensite telemetry (site-level).
-- Captures 1-hour averages of key OEM-agnostic channels used by O&M reporting.

CREATE TABLE IF NOT EXISTS gensite_hourly_metrics (
    id                          BIGSERIAL PRIMARY KEY,
    site_code                   TEXT NOT NULL REFERENCES sites(code) ON DELETE CASCADE,
    hour_utc                    TIMESTAMPTZ NOT NULL,
    avg_pv_kw                   NUMERIC(12, 4),
    avg_load_kw                 NUMERIC(12, 4),
    avg_genset_kw               NUMERIC(12, 4),
    avg_battery_soc_pct         NUMERIC(7, 3),
    sample_count                INTEGER NOT NULL DEFAULT 0,
    genset_inferred_from_grid   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gensite_hourly_metrics_site_hour UNIQUE (site_code, hour_utc)
);

CREATE INDEX IF NOT EXISTS idx_gensite_hourly_metrics_site_hour
    ON gensite_hourly_metrics (site_code, hour_utc DESC);

COMMENT ON TABLE gensite_hourly_metrics IS
'Hourly archival telemetry rollups per site: avg PV, load, inferred/explicit genset, and battery SoC.';
COMMENT ON COLUMN gensite_hourly_metrics.genset_inferred_from_grid IS
'TRUE when the hour used grid-import inference for genset power because no explicit genset equipment samples were present.';
