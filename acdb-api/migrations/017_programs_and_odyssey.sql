-- 017_programs_and_odyssey.sql
--
-- Funder / monitoring programs + Odyssey Standard API support.
--
-- Use case: UEF / ZEDSI (Zambia Energy Demand Stimulation Incentive) requires
-- a sponsor-monitoring API that exposes electricity payments and meter metrics
-- for a *subset* of customers. The same model also supports any future
-- funder programs (other RBF schemes, donor monitoring, etc.) without code
-- changes -- bind a token to a (program, country) pair, tag the in-program
-- accounts, and the existing ``GET /api/odyssey/v1/...`` endpoints serve only
-- those rows.
--
-- See:
--   * docs/odyssey-standard-api.md              (Standard API contract / runbook)
--   * docs/uef_zedsi_claim_template.xlsx        (Connections claim spreadsheet --
--                                                fields the API output should
--                                                semantically mirror)
--   * acdb-api/odyssey_api.py                   (read-only public API)
--   * acdb-api/programs.py                      (admin CRUD + bulk tagging)
--
-- Idempotent: safe to re-apply (every CREATE / ALTER uses IF NOT EXISTS).

BEGIN;

-- ---------------------------------------------------------------------------
-- programs: registry of funder / monitoring programs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS programs (
    id              BIGSERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,                  -- e.g. UEF_ZEDSI
    name            TEXT NOT NULL,
    funder          TEXT,                                  -- e.g. UEF / SEforAll
    country_code    TEXT,                                  -- ISO 3166-1 alpha-2 (LS / BN / ZM)
    description     TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_programs_country_active
    ON programs (country_code, active)
    WHERE active = TRUE;

COMMENT ON TABLE  programs              IS 'Registry of funder / monitoring programs (e.g. UEF_ZEDSI). Each program scopes a set of accounts that can be exposed via the Odyssey Standard API.';
COMMENT ON COLUMN programs.code         IS 'Stable machine identifier referenced by tokens and memberships (e.g. UEF_ZEDSI).';
COMMENT ON COLUMN programs.country_code IS 'Optional country scope; bulk-tag-by-country and token issuance use this for safety checks.';

-- Seed UEF_ZEDSI (idempotent).
INSERT INTO programs (code, name, funder, country_code, description, active, created_by)
VALUES (
    'UEF_ZEDSI',
    'UEF Zambia Energy Demand Stimulation Incentive',
    'UEF / SEforAll',
    'ZM',
    'Universal Energy Facility ZEDSI program -- Zambia mini-grid PUE-driven demand stimulation. Customers tagged here are exposed to Odyssey for sponsor monitoring.',
    TRUE,
    'system'
)
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- program_memberships: which accounts participate in which program
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS program_memberships (
    program_id          BIGINT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    account_number      TEXT   NOT NULL,
    joined_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claim_milestone     TEXT,                              -- e.g. 'Milestone 1', 'Milestone 2' (matches systemConfigurationHidden in claim template)
    pue_equipment       JSONB,                             -- mirrors PUE / demand-stimulation columns from the claim template
    notes               TEXT,
    added_by            TEXT,
    PRIMARY KEY (program_id, account_number)
);

CREATE INDEX IF NOT EXISTS idx_program_memberships_account
    ON program_memberships (account_number);

CREATE INDEX IF NOT EXISTS idx_program_memberships_program
    ON program_memberships (program_id, joined_at DESC);

COMMENT ON TABLE  program_memberships               IS 'Account-level membership in a funder program. account_number is the billable unit; one customer can be in multiple programs via multiple accounts (rare).';
COMMENT ON COLUMN program_memberships.claim_milestone IS 'UEF claim milestone label, e.g. ''Milestone 1''. Used to filter the Connections claim export.';
COMMENT ON COLUMN program_memberships.pue_equipment   IS 'Free-form JSON mirroring the spreadsheet PUE columns (Primary/Secondary/Tertiary equipment, brand, wattage, capex). Only populated when ops register the equipment for a claim.';

-- ---------------------------------------------------------------------------
-- odyssey_api_tokens: bearer tokens for the Standard API, scoped to a program
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS odyssey_api_tokens (
    id              BIGSERIAL PRIMARY KEY,
    program_id      BIGINT NOT NULL REFERENCES programs(id) ON DELETE RESTRICT,
    token_hash      TEXT NOT NULL UNIQUE,                  -- sha256(plaintext) -- plaintext shown once at issuance
    token_prefix    TEXT NOT NULL,                         -- first 8 chars of plaintext, for UI identification ("ody_a1b2...")
    label           TEXT NOT NULL,                         -- human-readable e.g. 'odyssey-uef-prod'
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    issued_by       TEXT,
    expires_at      TIMESTAMPTZ,                           -- NULL = no expiry; recommend 90 days
    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT,
    last_used_at    TIMESTAMPTZ,
    last_used_ip    TEXT
);

CREATE INDEX IF NOT EXISTS idx_odyssey_tokens_program
    ON odyssey_api_tokens (program_id, revoked_at);

COMMENT ON TABLE  odyssey_api_tokens             IS 'Bearer tokens for the Odyssey Standard API. Plaintext is shown to the issuer once at creation; only sha256 hash is stored.';
COMMENT ON COLUMN odyssey_api_tokens.token_hash  IS 'sha256 of the plaintext token. Fast lookup with a UNIQUE index.';
COMMENT ON COLUMN odyssey_api_tokens.token_prefix IS 'Plaintext prefix (first 8 chars) for the UI to identify which token was issued without revealing the secret.';
COMMENT ON COLUMN odyssey_api_tokens.expires_at  IS 'NULL = no expiry. Recommend 90-day rotation policy aligned with JWT secret hygiene.';

-- ---------------------------------------------------------------------------
-- customers: add the few fields ZEDSI / Odyssey expect that we don't already
-- have. Most are present already (gender, customer_type, national_id, district,
-- street_address, gps_lat/gps_lon). Two are new:
-- ---------------------------------------------------------------------------

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS simple_category TEXT;
    -- e.g. Residential / Commercial / Industrial / Institutional / Agriculture
    -- (matches customer.simpleCategoryHidden in the claim template).

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS previous_energy_source TEXT;
    -- e.g. Candles / Battery torches / Solar Home System / Petrol Generator /
    -- Diesel Generator / Main Grid / Kerosene Lamps / Other / Not Applicable
    -- (matches energySourceHidden in the claim template).

COMMENT ON COLUMN customers.simple_category        IS 'Funder-facing demographic bucket (Residential / Commercial / Industrial / Institutional / Agriculture). Maps to Odyssey customer.simpleCategory.';
COMMENT ON COLUMN customers.previous_energy_source IS 'Pre-connection energy source (Candles, Solar Home System, Diesel Generator, etc.). Maps to Odyssey energySource and supports demand-stim impact reporting.';

COMMIT;
