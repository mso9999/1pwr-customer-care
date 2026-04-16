# 1Meter MAK — field report vs 1PDB (CC)

## 2026-04-16 — Motlatsi (field)

1. Three additional meters installed this round; **10 meters installed total** (cumulative in field).
2. All **10** are still communicating (field observation).
3. More detailed report expected EOB **2026-04-17**.

## Production `onepower_cc` snapshot (CC Linux host, same day)

### Registered check meters (source of truth for **Check Meters** page)

The **Check Meters** UI (`/check-meters`, `om_report._build_check_meter_comparison`) lists one row per **`meters`** row with **`role = 'check'`** and **`status = 'active'`** that shares an **`account_number`** with an **active `role = 'primary'`** SparkMeter.

| meter_id (1M) | account | primary (SM) | last_seen_utc (prototype_meter_state) |
|---------------|---------|--------------|----------------------------------------|
| 23022628 | 0005MAK | SMRSD-03-0002E040 | see live DB |
| 23022696 | 0025MAK | SMRSDRF-01-0003E43F | see live DB |
| 23021847 | 0026MAK | SMRSD-03-00036DEE | see live DB |
| 23022673 | 0045MAK | SMRSD-03-0001A2B9 | see live DB |
| 23022646 | 0119MAK | SMRSDRF-01-0003EBB4 | see live DB |

**Count:** **5** active check-meter rows in **`meters`**, not 10.

### Gap vs field count (10)

Until the other **five** physical installs appear as **`meters`** rows (`platform = prototype`, `role = check`, `status = active`, correct **`account_number`**, and a **`primary`** meter on that account), they **will not** appear on **Check Meters**. Ops should add them via the normal commissioning / assign-meter flow when accounts and IDs are known.

### Decommissioned / stale hardware row

| meter_id | notes |
|----------|--------|
| 23022684 | **`decommissioned`** in **`meters`**; **`prototype_meter_state`** still has old **`last_seen_at`** (2026-03-11). Not in the active check list. |

## Firmware version (latest?)

**Customer Care / 1PDB does not store per-device firmware version.** `prototype_meter_state` holds energy, relay, **`last_seen_at`**, **`last_sample_time`** only. Fleet FW tracking is **AWS IoT OTA** / device shadow / **`onepwr-aws-mesh`** tooling (see archived runbook `docs/archive/2026-03-worktree-cleanup/1meter/1Meter-Remote-Build-OTA-Runbook.md`). To assert “all on latest FW”, use the OTA / IoT console or a device-reported version pipeline—not the Check Meters page.

## Repeatable queries (read-only)

```sql
-- Active 1M check meters + primary SM on same account
SELECT c.meter_id AS check_1m, c.account_number,
       p.meter_id AS primary_sm
FROM meters c
JOIN meters p ON p.account_number = c.account_number
  AND p.role = 'primary' AND p.status = 'active'
WHERE c.role = 'check' AND c.status = 'active'
ORDER BY c.account_number;

SELECT meter_id, account_number, last_seen_at
FROM prototype_meter_state
WHERE meter_id IN (SELECT meter_id FROM meters WHERE role = 'check' AND status = 'active')
ORDER BY meter_id;
```
