-- 067: meter_gateway_link — one row per customer meter -> its 1Meter gateway/PTB/pole.
--
-- This table was introduced by the assign-PTB flow (commit d518a7a) and read by
-- the fleet map, the meters "linked" filter, and the customer-linkage card, but
-- no migration ever created it. On a database where it is missing, the
-- fleet-map LEFT JOIN throws and the endpoint fails (or, where wrapped in
-- try/except, fails silently) — so linked meters never surface their Thing name.
--
-- meter_provisioning is one-row-per-gateway (primary meter only); it cannot
-- capture the many-meters-per-gateway PTB-channel links, so this CC-owned table
-- holds them. gateway_thing is nullable: assign-PTB records the link even when
-- the gateway has not reported yet (commit 2b2877f), and the gateway leg
-- auto-completes when the unit first reports the meter.

CREATE TABLE IF NOT EXISTS meter_gateway_link (
    meter_serial    VARCHAR(64) PRIMARY KEY,
    gateway_thing   VARCHAR(128),
    ptb_id          VARCHAR(64),
    pole_id         VARCHAR(64),
    account_number  VARCHAR(32),
    site            VARCHAR(16),
    linked_by       TEXT,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mgl_account
    ON meter_gateway_link (account_number);
CREATE INDEX IF NOT EXISTS idx_mgl_site
    ON meter_gateway_link (site);
CREATE INDEX IF NOT EXISTS idx_mgl_gateway
    ON meter_gateway_link (gateway_thing);
