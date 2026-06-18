-- Tranche 2a payment deduplication: within each EXACT (account, transaction_date
-- timestamp, amount) payment group that still has >1 row, keep the earliest row
-- (min id, the genuine original) and archive/remove the rest. Exact-timestamp +
-- exact-amount collisions for one account are the same payment recorded twice
-- (e.g. hist_repair re-inserts, feed double-inserts), never two real top-ups.
--
-- Dry-run:  psql -v do_commit=false -f dedup_payments_tranche2a.sql
-- Apply:    psql -v do_commit=true  -f dedup_payments_tranche2a.sql
\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE IF NOT EXISTS recon_deleted_dupes_20260618 (LIKE transactions);

WITH groups AS (
    SELECT account_number, transaction_date, round(transaction_amount::numeric, 2) AS amt, min(id) AS keep_id
    FROM transactions
    WHERE is_payment AND source NOT IN ('balance_seed', 'opening_anchor')
    GROUP BY 1, 2, 3
    HAVING count(*) > 1
),
del AS (
    DELETE FROM transactions t
    USING groups g
    WHERE t.is_payment AND t.source NOT IN ('balance_seed', 'opening_anchor')
      AND t.account_number = g.account_number
      AND t.transaction_date = g.transaction_date
      AND round(t.transaction_amount::numeric, 2) = g.amt
      AND t.id <> g.keep_id
    RETURNING t.*
)
INSERT INTO recon_deleted_dupes_20260618 SELECT * FROM del;

SELECT 'total_archived_so_far' AS metric, count(*) AS value FROM recon_deleted_dupes_20260618
UNION ALL
SELECT 'remaining_exact_ts_dupes', count(*) FROM (
    SELECT 1 FROM transactions WHERE is_payment AND source NOT IN ('balance_seed','opening_anchor')
    GROUP BY account_number, transaction_date, round(transaction_amount::numeric,2) HAVING count(*)>1
) z;

\if :do_commit
    COMMIT;
    \echo '>>> COMMITTED'
\else
    ROLLBACK;
    \echo '>>> DRY-RUN rolled back'
\endif
