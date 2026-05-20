-- Manual cohort funnel status (Customer Cohort page + customer dashboard).
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS cohort_status_override TEXT,
    ADD COLUMN IF NOT EXISTS cohort_status_override_by TEXT,
    ADD COLUMN IF NOT EXISTS cohort_status_override_at TIMESTAMPTZ;

COMMENT ON COLUMN customers.cohort_status_override IS
    'When set, overrides computed cohort_status in Customer Cohort queries.';
