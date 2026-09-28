-- Operator-chosen firmware targets from the meters map and list.
-- This queue is not the factory site release. Promote still reads
-- onemeter_ota_site_releases and is not updated by these rows.

CREATE TABLE IF NOT EXISTS onemeter_ota_queue (
    id                   BIGSERIAL PRIMARY KEY,
    batch_id             UUID NOT NULL,
    site_code            VARCHAR(16) NOT NULL,
    thing_name           VARCHAR(128) NOT NULL,
    meter_ids            TEXT[] NOT NULL DEFAULT '{}',
    target_version       VARCHAR(32) NOT NULL,
    artifact_key         TEXT NOT NULL,
    artifact_version_id  TEXT NOT NULL,
    current_fw           VARCHAR(32),
    status               VARCHAR(16) NOT NULL DEFAULT 'pending',
    ota_update_id        TEXT,
    job_id               TEXT,
    detail               TEXT,
    created_by           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    kicked_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_onemeter_ota_queue_site_status
    ON onemeter_ota_queue (site_code, status);

CREATE INDEX IF NOT EXISTS idx_onemeter_ota_queue_batch
    ON onemeter_ota_queue (batch_id);

CREATE UNIQUE INDEX IF NOT EXISTS onemeter_ota_queue_one_open
    ON onemeter_ota_queue (thing_name)
    WHERE status IN ('pending', 'active', 'held');
