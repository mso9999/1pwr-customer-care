-- 065: PR → CC site sync + uGP design association
--
-- Sites are born in PR (pre-survey spend), get exactly one canonical uGP
-- design after survey, and enter CC at commissioning.  PR fans site events
-- out to each CC lane (POST /api/site-sync/ingest); the lane upserts into
-- country_sites with source='pr' and staged active=FALSE until someone with
-- manage_site_registry activates the site locally.
--
-- ugp_project_ids / canonical_ugp_project_id mirror the PR registry's uGP
-- links so CC can warn when a site is being activated without its canonical
-- design — the "someone missed a step" remediation signal.

ALTER TABLE country_sites
    ADD COLUMN IF NOT EXISTS ugp_project_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS canonical_ugp_project_id text;

-- Idempotency ledger for fanout deliveries (PR retries on 5xx).
CREATE TABLE IF NOT EXISTS site_sync_events (
    idempotency_key text PRIMARY KEY,
    event_type      text NOT NULL,
    site_code       text NOT NULL,
    organization_id text NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now()
);
