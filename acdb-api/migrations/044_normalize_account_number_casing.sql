-- 044: structural guard against account-number casing fragmentation
--
-- RCA 2026-06-18: the same physical account was stored under multiple casings
-- (e.g. 0020MAK / 0020MAk / 0020Mak / 0020mak). The master `accounts` table is
-- 100% canonical UPPERCASE and Koios uses uppercase, but SMS/manual payment
-- inserts sometimes wrote lower/mixed case, orphaning ~13,485 kWh of payments on
-- "ghost" identities (real account under-credited; ghost shows SM=0 drift).
-- Existing rows were normalized via scripts/ops/casing_normalize.sql.
--
-- This trigger makes the invariant structural: account_number is forced to
-- canonical uppercase on INSERT/UPDATE for the low-volume financial/identity
-- tables, so no future code path (SMS gateway, manual credit mirror, portal entry)
-- can re-introduce a wrong-case identity. hourly_consumption is intentionally
-- excluded: it already arrives uppercase from the Koios import and is far too
-- high-volume to justify a per-row trigger.

CREATE OR REPLACE FUNCTION normalize_account_number_upper()
RETURNS trigger AS $$
BEGIN
    IF NEW.account_number IS NOT NULL THEN
        NEW.account_number := upper(NEW.account_number);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_transactions_acct_upper ON transactions;
CREATE TRIGGER trg_transactions_acct_upper
    BEFORE INSERT OR UPDATE OF account_number ON transactions
    FOR EACH ROW EXECUTE FUNCTION normalize_account_number_upper();

DROP TRIGGER IF EXISTS trg_meters_acct_upper ON meters;
CREATE TRIGGER trg_meters_acct_upper
    BEFORE INSERT OR UPDATE OF account_number ON meters
    FOR EACH ROW EXECUTE FUNCTION normalize_account_number_upper();

DROP TRIGGER IF EXISTS trg_meter_assignments_acct_upper ON meter_assignments;
CREATE TRIGGER trg_meter_assignments_acct_upper
    BEFORE INSERT OR UPDATE OF account_number ON meter_assignments
    FOR EACH ROW EXECUTE FUNCTION normalize_account_number_upper();
