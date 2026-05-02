-- 018_coverage_snapshots.sql
--
-- Daily snapshots of 1PDB coverage gaps so trends are visible from the
-- portal admin UI (``/admin/coverage``) without having to SSH and re-run
-- ``scripts/ops/audit_coverage_gaps.py`` every time.
--
-- Each snapshot row captures the totals from one audit run; the full
-- per-site / per-meter detail is stored in JSONB columns. This keeps the
-- table small (one row per (snapshot_at, country) pair) but lets us
-- reconstruct any historical view.
--
-- The audit data is written to this table by:
--   * ``scripts/ops/audit_coverage_gaps.py --snapshot`` (systemd timer)
--   * ``POST /api/admin/coverage/snapshot`` (manual trigger from the UI)
--
-- See:
--   * docs/ops/coverage-audit-2026-05-02-triage.md (initial findings)
--   * acdb-api/coverage_audit.py (admin endpoints)

BEGIN;

CREATE TABLE IF NOT EXISTS coverage_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    country_code    TEXT NOT NULL,                        -- LS, BN, ZM, ...

    -- Roll-up totals (cheap to render in lists / dashboards)
    active_meters             INTEGER NOT NULL DEFAULT 0,
    zero_coverage_meters      INTEGER NOT NULL DEFAULT 0,
    stale_meters              INTEGER NOT NULL DEFAULT 0,
    monthly_deficits_flagged  INTEGER NOT NULL DEFAULT 0,
    sites_with_active_meters  INTEGER NOT NULL DEFAULT 0,
    sites_with_data           INTEGER NOT NULL DEFAULT 0,

    -- Audit knobs at the time of the snapshot (useful when comparing runs
    -- across config changes).
    window_months         INTEGER NOT NULL,
    stale_days            INTEGER NOT NULL,
    deficit_threshold     DOUBLE PRECISION NOT NULL,

    -- Detail blobs -- structure mirrors the script's JSON output for
    -- exact parity with ``audit_coverage_gaps.py``. Kept JSONB so we can
    -- query / index specific fields later if the read pattern justifies it.
    monthly_coverage         JSONB NOT NULL DEFAULT '{}'::jsonb,
    monthly_deficits         JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_ingest              JSONB NOT NULL DEFAULT '{}'::jsonb,
    zero_coverage_summary    JSONB NOT NULL DEFAULT '{}'::jsonb,
    cross_country_meters     JSONB NOT NULL DEFAULT '[]'::jsonb,
    declared_sites_missing   JSONB NOT NULL DEFAULT '[]'::jsonb,
    orphan_sites             JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Per-meter detail can be large (300+ rows in current LS state); store
    -- separately so we can lazy-load on demand.
    zero_coverage_meters_detail  JSONB NOT NULL DEFAULT '[]'::jsonb,
    stale_meters_detail          JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Optional: upstream freshness (Koios v2 data/freshness, TC parquet
    -- list). Populated by the upstream-reconciliation path which is
    -- network-bound and may be skipped.
    upstream_freshness       JSONB,
    upstream_checked_at      TIMESTAMPTZ,

    triggered_by  TEXT,                                  -- 'timer', 'admin:<user_id>', 'cli'
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_country_at
    ON coverage_snapshots (country_code, snapshot_at DESC);

CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_at
    ON coverage_snapshots (snapshot_at DESC);

COMMENT ON TABLE  coverage_snapshots             IS 'Daily snapshots of the 1PDB coverage audit. One row per (snapshot_at, country_code) pair.';
COMMENT ON COLUMN coverage_snapshots.country_code IS 'Country whose 1PDB this snapshot describes (LS, BN, ZM, ...).';
COMMENT ON COLUMN coverage_snapshots.upstream_freshness IS 'Optional Koios v2 / TC freshness blob for source-side gap diff. Populated only when the upstream reconciliation path was executed.';
COMMENT ON COLUMN coverage_snapshots.triggered_by  IS 'How this snapshot was taken (systemd ''timer'', manual ''admin:<user>'', or ''cli'').';

COMMIT;
