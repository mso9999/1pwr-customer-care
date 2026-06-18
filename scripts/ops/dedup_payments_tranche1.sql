-- Tranche 1 payment deduplication: remove credit-mirror (sm_manual_hist) rows that
-- are EXACT duplicates (same account, same transaction_date timestamp, same amount)
-- of an existing non-mirror original payment. The original is kept; only the mirror
-- re-insert is removed. Every removed row is archived to recon_deleted_dupes_20260618.
--
-- Run dry-run:  psql -v do_commit=false -f dedup_payments_tranche1.sql
-- Run apply:    psql -v do_commit=true  -f dedup_payments_tranche1.sql
\set ON_ERROR_STOP on
BEGIN;

CREATE TABLE IF NOT EXISTS recon_deleted_dupes_20260618 (LIKE transactions);

WITH del AS (
    DELETE FROM transactions m
    WHERE m.is_payment
      AND m.payment_reference ILIKE 'sm_manual_hist%'
      AND EXISTS (
          SELECT 1 FROM transactions o
          WHERE o.is_payment
            AND o.id <> m.id
            AND o.account_number = m.account_number
            AND o.transaction_date = m.transaction_date
            AND abs(COALESCE(o.transaction_amount, 0) - COALESCE(m.transaction_amount, 0)) < 0.01
            AND COALESCE(o.payment_reference, '') NOT ILIKE 'sm_manual_hist%'
      )
    RETURNING m.*
)
INSERT INTO recon_deleted_dupes_20260618 SELECT * FROM del;

SELECT 'archived_deleted_rows' AS metric, count(*) AS value FROM recon_deleted_dupes_20260618
UNION ALL
SELECT 'remaining_exact_twin_mirror_dupes', count(*) FROM transactions m
WHERE m.is_payment AND m.payment_reference ILIKE 'sm_manual_hist%'
  AND EXISTS (SELECT 1 FROM transactions o WHERE o.is_payment AND o.id<>m.id
      AND o.account_number=m.account_number AND o.transaction_date=m.transaction_date
      AND abs(COALESCE(o.transaction_amount,0)-COALESCE(m.transaction_amount,0))<0.01
      AND COALESCE(o.payment_reference,'') NOT ILIKE 'sm_manual_hist%')
UNION ALL
SELECT 'archived_kwh', round(sum(kwh_value)::numeric,0) FROM recon_deleted_dupes_20260618;

\if :do_commit
    COMMIT;
    \echo '>>> COMMITTED'
\else
    ROLLBACK;
    \echo '>>> DRY-RUN rolled back'
\endif
