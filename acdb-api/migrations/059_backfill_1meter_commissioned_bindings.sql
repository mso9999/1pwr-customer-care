-- Preserve existing one-to-one prototype deployments when gateway identity is
-- added to telemetry. Only unambiguous normalized serial matches are promoted;
-- historic alias/duplicate Things stay blocked for operator reconciliation.

WITH matches AS (
    SELECT
        mp.id AS provisioning_id,
        regexp_replace(mp.meter_serial, '^0+', '') AS normalized_serial,
        m.account_number,
        COUNT(*) OVER (
            PARTITION BY regexp_replace(mp.meter_serial, '^0+', '')
        ) AS match_count
    FROM meter_provisioning mp
    JOIN meters m
      ON regexp_replace(m.meter_id, '^0+', '') =
         regexp_replace(mp.meter_serial, '^0+', '')
     AND m.platform = 'prototype'
     AND m.status = 'active'
     AND NULLIF(m.account_number, '') IS NOT NULL
    WHERE NULLIF(mp.meter_serial, '') IS NOT NULL
      AND (
          NULLIF(mp.account_number, '') IS NULL
          OR UPPER(mp.account_number) = UPPER(m.account_number)
      )
),
safe_matches AS (
    SELECT provisioning_id, account_number
    FROM matches
    WHERE match_count = 1
)
UPDATE meter_provisioning mp
   SET account_number = UPPER(safe.account_number),
       status = 'commissioned',
       commissioned_at = COALESCE(
           mp.commissioned_at,
           mp.first_seen_online,
           mp.provisioned_at,
           NOW()
       ),
       updated_at = NOW()
  FROM safe_matches safe
 WHERE mp.id = safe.provisioning_id
   AND mp.status <> 'commissioned';
