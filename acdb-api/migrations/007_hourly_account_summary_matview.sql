BEGIN;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_hourly_account_summary AS
SELECT account_number,
       COUNT(DISTINCT reading_hour)::bigint AS distinct_hours,
       MIN(reading_hour)                    AS first_record_at,
       MAX(reading_hour)                    AS last_record_at
FROM hourly_consumption
GROUP BY account_number;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_hourly_acct_summary_acct
    ON mv_hourly_account_summary (account_number);

COMMIT;
