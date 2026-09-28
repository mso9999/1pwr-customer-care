-- Site electricity-billing hold, plus a per-meter override that bills
-- one meter while the rest of the site is still waiting on inspection.

BEGIN;

CREATE TABLE IF NOT EXISTS site_electricity_holds (
    id          BIGSERIAL PRIMARY KEY,
    site_code   TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    reason      TEXT NOT NULL,
    started_by  TEXT,
    ended_by    TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_site_electricity_holds_open
    ON site_electricity_holds (site_code)
    WHERE ended_at IS NULL;

COMMENT ON TABLE site_electricity_holds IS
    'While a row is open, meters at this site supply electricity free. '
    'Hours inside the window stay out of the prepaid balance after the row ends.';

CREATE TABLE IF NOT EXISTS meter_electricity_overrides (
    id              BIGSERIAL PRIMARY KEY,
    meter_id        TEXT NOT NULL,
    account_number  TEXT NOT NULL,
    site_code       TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    reason          TEXT NOT NULL,
    started_by      TEXT,
    ended_by        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_meter_electricity_overrides_open
    ON meter_electricity_overrides (meter_id)
    WHERE ended_at IS NULL;

COMMENT ON TABLE meter_electricity_overrides IS
    'While a row is open, this meter is billed even if its site is on an inspection hold. '
    'Hours inside the window count. Hours before it, during a site hold, stay free.';

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_payment_category_check;

ALTER TABLE transactions
    ADD CONSTRAINT transactions_payment_category_check
    CHECK (payment_category IN (
        'electricity',
        'electricity_held',
        'connection_fee',
        'readyboard_fee',
        'uncategorized',
        'fee_advance_sms'
    ));

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS held_electricity_amount NUMERIC(14, 2);

COMMENT ON COLUMN transactions.held_electricity_amount IS
    'Electricity slice saved during a site hold. Released to kWh at rate_used when the site is cleared.';

COMMIT;
