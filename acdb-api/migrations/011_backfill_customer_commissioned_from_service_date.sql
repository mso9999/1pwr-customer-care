-- Align customer_commissioned with CC UI and commissioning workflow.
--
-- Root cause: Customer detail / list treat "active service" as
--   date_service_connected IS NOT NULL AND date_service_terminated IS NULL
-- (see CustomerDetailPage isCommissioned). The customer_commissioned boolean
-- is only set when POST /api/commission/execute completes. Legacy imports,
-- manual updates, or partial failures can leave date_service_connected set while
-- customer_commissioned stays false — exports and spreadsheets then disagree
-- with what staff see in the portal.

BEGIN;

UPDATE customers c
SET
  customer_commissioned = true,
  customer_commissioned_date = COALESCE(
    c.customer_commissioned_date,
    (c.date_service_connected)::date
  )
WHERE c.date_service_connected IS NOT NULL
  AND c.customer_commissioned = false
  AND c.date_service_terminated IS NULL;

COMMIT;
