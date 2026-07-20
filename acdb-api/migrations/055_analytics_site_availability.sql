-- Migration 055: Site availability table (SCADA / SMA / Victron)
-- Stores monthly site availability percentages from SCADA systems.

BEGIN;

CREATE TABLE IF NOT EXISTS site_availability (
    id SERIAL PRIMARY KEY,
    site_code VARCHAR(3) NOT NULL,
    period VARCHAR(7) NOT NULL,
    availability_pct NUMERIC(5,2),
    downtime_hours NUMERIC(10,2),
    source VARCHAR(20) NOT NULL,
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (site_code, period, source)
);

CREATE INDEX IF NOT EXISTS idx_site_availability_period
    ON site_availability (period);

COMMIT;
