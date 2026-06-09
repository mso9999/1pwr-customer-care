-- Rollback for the 2026-06-04 merchant-backfill date-transposition repair.
--
-- The repair (scripts/ops/repair_merchant_date_transposition.py --apply) saved
-- every changed row's original transaction_date into the backup table
-- ``transactions_date_repair_20260604`` (columns id, kind, account_number,
-- receipt, old_transaction_date, new_transaction_date, repaired_at).
--
-- To FULLY revert the repair, restore the old timestamps:
--
--   scripts/ops/ccdb.sh -f scripts/ops/rollback_merchant_date_transposition.sql
--
-- This is idempotent and safe to re-run. After verifying, the backup table can
-- be kept for audit or dropped manually.

BEGIN;

-- Optional safety check: show how many rows still match the repaired value
-- (i.e. have not been changed again since the repair).
\echo == rows currently AT repaired value (will be reverted) ==
SELECT count(*)
FROM transactions t
JOIN transactions_date_repair_20260604 b ON b.id = t.id
WHERE t.transaction_date = b.new_transaction_date;

\echo == rows that DIVERGED since repair (NOT auto-reverted; review manually) ==
SELECT t.id, t.account_number, t.transaction_date AS current_dt,
       b.old_transaction_date, b.new_transaction_date
FROM transactions t
JOIN transactions_date_repair_20260604 b ON b.id = t.id
WHERE t.transaction_date <> b.new_transaction_date
LIMIT 50;

-- Revert ONLY rows still holding the repaired value, so we never clobber a
-- subsequent legitimate edit.
UPDATE transactions t
   SET transaction_date = b.old_transaction_date
  FROM transactions_date_repair_20260604 b
 WHERE t.id = b.id
   AND t.transaction_date = b.new_transaction_date;

\echo == reverted row count ==
-- (psql prints the UPDATE count above as "UPDATE <n>")

COMMIT;
