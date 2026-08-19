-- UI-managed canonical site registry.
--
-- Historically the canonical site roster lived only in country_config.py
-- (site_abbrev), so adding a deployment site required a code change and a
-- redeploy of the country lane. This table backs the CC admin UI site
-- registry (Nexus ``manage_site_registry`` action, Engineering / IS&T):
-- active rows are overlaid on the static config by
-- country_config.live_site_abbrev().
--
-- Codes are globally unique across countries: gateway thing names
-- (``<SITE>-GW-####``) and customer account suffixes bind to the code for
-- life and must never collide or be reused.

CREATE TABLE IF NOT EXISTS country_sites (
    country_code  text        NOT NULL,              -- ISO-2 lane, e.g. 'ZM'
    code          text        NOT NULL,              -- 3-letter uppercase, e.g. 'CHI'
    name          text        NOT NULL,              -- official display name
    district      text,                              -- district / province
    active        boolean     NOT NULL DEFAULT TRUE,
    source        text        NOT NULL DEFAULT 'ui', -- 'ui' (vs code-seeded config)
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    retired_by    text,
    retired_at    timestamptz,
    PRIMARY KEY (country_code, code)
);

-- Globally unique code across every country (partial index: only active rows
-- are enforced; a retired code stays reserved by the registry API so it can
-- never be reused for a different site).
CREATE UNIQUE INDEX IF NOT EXISTS country_sites_active_code_key
    ON country_sites (code)
    WHERE active;
