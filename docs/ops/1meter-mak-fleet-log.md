# 1Meter MAK — field report vs 1PDB (CC)

> **Phase 1 test population.** MAK is the test bed for the
> [1Meter billing migration protocol](./1meter-billing-migration-protocol.md):
> SparkMeter is the billing meter; 1Meter is run alongside as a "what-if"
> check; the per-pair deviation watched on the Check Meters page is the
> input to the Phase 1 -> Phase 2 entry decision. Fleet billing default
> is `sm`; per-account overrides via `PATCH /api/billing-priority/{account}`.

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

## 2026-04-17 — Field update (Motlatsi) + CC action

> 1. MAK mission cancelled (rain).
> 2. All meters still communicating.
> 3. New meters:
>    - 23022684, 0051MAK, OneMeter19, Mathabo Ntlatlapo, 58402356
>    - 23021886, 0056MAK, OneMeter122, Matisetso Ntlatlapo, 51963779
>    - 23021888, 0058MAK, OneMeter12, Mathato Rapuleng, 59020758
> 4. ~90 % of external-antenna PCBs now deployed (better Wi-Fi); **stock exhausted**.

### Actioned in `onepower_cc`

- **`23022684`** reactivated → `role=check`, `status=active`, `account_number='0051MAK'`, `community='MAK'`, `platform='prototype'` (was `decommissioned`).
- **`23021886`** inserted → `0056MAK`.
- **`23021888`** inserted → `0058MAK`.
- **`prototype_meter_state`** reassigned `23022684` to `0051MAK` (stale row). Next IoT sample will also update via fixed UPSERT (now carries `account_number` on conflict).

### Portal after change

`/check-meters` pairs: **8** (`0005MAK, 0025MAK, 0026MAK, 0045MAK, 0051MAK, 0056MAK, 0058MAK, 0119MAK`). `0051MAK` `last_seen_at` will refresh when Lambda posts the next sample for `23022684`. `0056MAK` / `0058MAK` have no `prototype_meter_state` row yet — created on first IoT reading.

### Still not in check list (by design)

- **`23022667`** — gateway/repeater at powerhouse.
- **`23022613`** — repeater.

## 2026-04-17 — AWS `meter_last_seen` vs S3 vs 1PDB

### S3 (`s3://1meterdatacopy/`)

The bucket currently holds a **small sample** file (`1meter_data_s3_copy.json`, on the order of hundreds of bytes) — **not** a full fleet export. It is **not** a reliable place to count “10 meters.” Use **DynamoDB** or **1PDB** for inventory.

### DynamoDB `meter_last_seen` (region `us-east-1`)

A full table scan shows **10** rows (live telemetry index). Canonical IDs are **12-digit** `meterId`:

| meterId (Dynamo) | Short serial | In `meters` (1PDB)? | Role (from ops / `CONTEXT.md`) |
|------------------|--------------|---------------------|----------------------------------|
| 000023021847 | 23021847 | Yes | Customer check (0026MAK) |
| 000023021886 | 23021886 | **No** | Unmapped — needs account before CC row |
| 000023021888 | 23021888 | **No** | Unmapped — needs account before CC row |
| 000023022613 | 23022613 | **No** | **Repeater** (powerhouse) — **not** a Check Meter pair |
| 000023022628 | 23022628 | Yes | Customer check (0005MAK) |
| 000023022646 | 23022646 | Yes | Customer check (0119MAK) |
| 000023022667 | 23022667 | **No** | **Gateway** — **not** a Check Meter pair |
| 000023022673 | 23022673 | Yes | Customer check (0045MAK) |
| 000023022684 | 23022684 | Yes (decommissioned) | Was check; hardware swap / decom per ops |
| 000023022696 | 23022696 | Yes | Customer check (0025MAK) |

So: **10 online in AWS** matches the field team; **only six** serials have (or had) a **`meters`** row, and **five** are active **customer check** pairs on the Check Meters page. **Do not** register the **gateway** or **repeater** as `role = check` with a fake account — they are not SparkMeter comparison sites.

### Adding missing devices to the Check Meters **portal**

The UI only shows **primary + check** on the **same `account_number`**. Actionable steps:

1. **23021886, 23021888** — Obtain the **MAK account code(s)** from the field team (one account per customer check install). Then add **`meters`** rows via **Assign Meter / commissioning** or a controlled **`INSERT INTO meters`** with `platform = prototype`, `role = check`, `status = active`, and the correct `account_number` where a **primary** SparkMeter already exists (see `ingest.py` meter resolution and `GET /api/meters/account/{account}`).
2. **23022613, 23022667** — **Exclude** from Check Meters (infrastructure). Optionally add **`meters`** rows with `role = backup` or a dedicated non-billing role **only if** you want them in the registry for visibility — not as `check`.
3. **23022684** — Reconcile with ops (DDS swap / recommission) before reactivating.

**No safe automated INSERT** was applied from this repo without confirmed **account ↔ serial** mappings.

### Firmware — “all on latest?”

**Cannot be validated as yes/no from 1PDB.** Check IoT:

- **OTA jobs** (example `us-east-1`): `AFR_OTA-1meter-ota-v1-0-8-20260415204200` was **IN_PROGRESS** targeting **one** thing (`OneMeter13` / **23022673**). Older jobs (e.g. `AFR_OTA-live-v1_0_2-MAKGroup`) show **mixed** execution history (many **CANCELED** per-thing).
- **Thing attributes** in IoT are often **empty** — version is not reliably stored on the Thing object in this account.

To claim fleet-wide “latest,” you need either **device-reported version** (shadow / MQTT), **per-thing job SUCCESS**, or a **single job** targeting the full **thing group** with all devices succeeded — which is **not** currently evidenced for all 10.

## 2026-04-28 — Field update (Motlatsi) + v1.1.3 OTA canary

> 1. **Firmware status:** v1.1.1 confirmed flashed on every PCB *except* the
>    three newest installs (23022684 / 23021886 / 23021888). Those still
>    predate the FW-publish patch and therefore don't appear with a
>    `FirmwareVersion` in MQTT telemetry. They will get serial-flashed to
>    the current build during the next field visit.
> 2. **3 more meters added today** but none picked up comms — installed
>    far from the powerhouse toward the MAK cemetery, on built-in (low-gain)
>    antenna PCBs because external-antenna stock is exhausted (see
>    2026-04-17 note).
> 3. **OTA target preference (per field team):** use `OneMeter13`
>    (`23022673`, `0045MAK`) as the canary device. Confirm the build's
>    Wi-Fi creds are `MAK_Wifi-ext` / `1PWR_M@k123` *before* publishing.
> 4. **23022673 intermittent dropout** turned out to be the PCB failing
>    to read its own DDS8888 (same symptom seen earlier on `23022628` and
>    fixed there by changing the Modbus ID). The Wi-Fi link itself was
>    fine. Tracked as a hardware/firmware bug; cause unknown.

### Actioned in CC + AWS

| Action | Detail |
|--------|--------|
| **Pulled the v1.1.2 OTA** (built earlier today targeting `MAK_V1_1_1`) | The artifact had been built without `SITE_CONFIG=site-configs/MAK.conf` and embedded the build-host default `DareMightyThings`/`bestcity`. `app_wifi_init()` applies build-time SSID/password unconditionally on every boot — no NVS override path — so any device that completed the OTA would have bricked on Wi-Fi. Cancelled and deleted before any device finished the download. |
| **Built v1.1.3 with `SITE_CONFIG=site-configs/MAK.conf`** | `release-manifest.json` confirms `router_ssid: MAK_Wifi-ext`, `router_password_set: true`. Bumped to 1.1.3 to keep the contaminated 1.1.2 label distinguishable in the OTA / S3 history. Commit: `onepwr-aws-mesh@f16ff3d`. |
| **Patched `build_firmware_remote.sh` with a fail-closed Wi-Fi-SSID guard** | Refuses to leave a release on disk if the embedded SSID is `DareMightyThings` unless `ALLOW_DEFAULT_WIFI=1`. Verified by triggering it. Mirror in the CC repo at `scripts/ops/build_firmware_remote.sh`; runbook at `docs/ops/1meter-build-and-ota-runbook.md`. |
| **Created v1.1.3 single-thing OTA canary** | `1m-v1-1-3-canary-OneMeter13-20260428163531`, target ARN `…thing/OneMeter13`. Picked up by the device in <1 minute (vs the 3 h `IN_PROGRESS`-stuck pattern of the 1.1.2 attempt — which was probably IoT scheduler latency rather than the cert-mismatch hypothesis). |

### MAK fleet snapshot (DynamoDB telemetry, 2026-04-28 ~18:30 SAST)

| Serial | Role | Account | Last sample | FW | Notes |
|---|---|---|---|---|---|
| 23022613 | repeater | — | <1 min | 1.1.1 | |
| 23022673 | check | 0045MAK (OneMeter13) | <1 min | 1.1.1 → **1.1.3 OTA in flight** | |
| 23022628 | check | 0005MAK | <2 min | 1.1.1 | |
| 23021847 | check | 0026MAK | <3 min | 1.1.1 | |
| 23022696 | check | 0025MAK | ~1 h | 1.1.1 | |
| 23022684 | check | 0051MAK | ~1.3 h | — | Schyler batch, no FW publish; needs serial reflash |
| 23022646 | check | 0119MAK | ~4 h | 1.1.1 | |
| 23021888 | check | 0058MAK | ~5.6 h | — | Schyler batch, no FW publish; needs serial reflash |
| 23022667 | gateway | — | ~7 h | 1.1.1 | |
| 23021886 | check | 0056MAK | **~4 days** | — | Schyler batch + offline since 24 Apr — flagged to field team |

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
