-- Account-number casing normalization (RCA 2026-06-18)
-- ROOT CAUSE: the same physical account was stored under multiple casings
--   (e.g. 0020MAK / 0020MAk / 0020Mak / 0020mak). The master `accounts` table
--   is 100% canonical UPPERCASE and Koios uses uppercase, but SMS/manual payment
--   inserts sometimes used lower/mixed case. Consumption (Koios import) always
--   lands on the uppercase account, so payments on a wrong-case "ghost" identity
--   were orphaned: the real account is UNDER-credited (engine too low / negative)
--   while the ghost shows payments with no consumption (the SM=0 "+200" drifters).
--
-- SAFETY: no UNIQUE/PK constraint on any of these tables includes account_number
--   (verified: transactions PK(id); meters PK(id)+UNIQUE(meter_id);
--    meter_assignments PK(id); hourly_consumption PK(id,reading_hour)+
--    UNIQUE(meter_id,reading_hour)). Upper-casing account_number therefore cannot
--   violate any unique constraint -> a pure, collision-free relabel. Fully
--   reversible via the casing_bak_* tables created below.
--
-- Run with:  psql -d <db> -v ON_ERROR_STOP=1 -f casing_normalize.sql

\set ON_ERROR_STOP on
BEGIN;

-- 1) Back up exactly the rows we are about to change (idempotent: drop+recreate)
DROP TABLE IF EXISTS casing_bak_transactions_20260618;
CREATE TABLE casing_bak_transactions_20260618 AS
  SELECT * FROM transactions WHERE account_number <> upper(account_number);

DROP TABLE IF EXISTS casing_bak_hourly_consumption_20260618;
CREATE TABLE casing_bak_hourly_consumption_20260618 AS
  SELECT * FROM hourly_consumption WHERE account_number <> upper(account_number);

DROP TABLE IF EXISTS casing_bak_meters_20260618;
CREATE TABLE casing_bak_meters_20260618 AS
  SELECT * FROM meters WHERE account_number <> upper(account_number);

DROP TABLE IF EXISTS casing_bak_meter_assignments_20260618;
CREATE TABLE casing_bak_meter_assignments_20260618 AS
  SELECT * FROM meter_assignments WHERE account_number <> upper(account_number);

-- 2) Show pre-change counts
\echo '== rows to normalize =='
SELECT 'transactions'      AS tbl, count(*) FROM casing_bak_transactions_20260618
UNION ALL SELECT 'hourly_consumption', count(*) FROM casing_bak_hourly_consumption_20260618
UNION ALL SELECT 'meters',            count(*) FROM casing_bak_meters_20260618
UNION ALL SELECT 'meter_assignments', count(*) FROM casing_bak_meter_assignments_20260618;

-- 3) Normalize to canonical uppercase
UPDATE transactions       SET account_number = upper(account_number) WHERE account_number <> upper(account_number);
UPDATE hourly_consumption SET account_number = upper(account_number) WHERE account_number <> upper(account_number);
UPDATE meters             SET account_number = upper(account_number) WHERE account_number <> upper(account_number);
UPDATE meter_assignments  SET account_number = upper(account_number) WHERE account_number <> upper(account_number);

-- 4) Verify zero residual non-canonical rows remain
\echo '== residual non-canonical (must all be 0) =='
SELECT 'transactions'      AS tbl, count(*) FROM transactions       WHERE account_number <> upper(account_number)
UNION ALL SELECT 'hourly_consumption', count(*) FROM hourly_consumption WHERE account_number <> upper(account_number)
UNION ALL SELECT 'meters',            count(*) FROM meters             WHERE account_number <> upper(account_number)
UNION ALL SELECT 'meter_assignments', count(*) FROM meter_assignments  WHERE account_number <> upper(account_number);

COMMIT;
