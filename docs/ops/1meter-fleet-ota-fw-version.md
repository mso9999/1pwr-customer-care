# Fleet OTA + firmware version visibility (1Meter)

End state: every device runs a **new** build that **publishes** its version string on the normal telemetry path, **`ingestion_gate`** forwards it to CC, and **`prototype_meter_state.firmware_version`** + **Check Meters** show it.

## 1) Firmware (`onepowerLS/onepwr-aws-mesh`) — **committed `21b8586`**

- MQTT publish task (`main/tasks/onemeter_mqtt/onemeter_mqtt.c`) adds **`"FirmwareVersion": "%d.%d.%d"`** from `APP_VERSION_MAJOR/MINOR/BUILD` (via `ota_over_mqtt_demo_config.h`). Sent on every sample.
- **`sdkconfig.defaults`**: bumped to **`1.1.0`** (`MAJOR=1, MINOR=1, BUILD=0`) so the new image is strictly higher than the deployed `1.0.8`.
- Build + OTA via `/opt/1meter-firmware` on staging EC2 (see runbook in `docs/archive/2026-03-worktree-cleanup/1meter/1Meter-Remote-Build-OTA-Runbook.md`).

## 2) Ingestion (`onepowerLS/ingestion_gate` Lambda) — **committed `67f5ab3`**

- `meter_ingest_gate.py` scans incoming payload for common FW keys (`FirmwareVersion`, `firmware_version`, `AppVersion`, `OTAAppVersion`, …) and forwards as **`firmware_version`** to CC `/api/meters/reading`. No change to `X-IoT-Key` contract.

## 3) Customer Care API (this repo) — deployed with migration **`012_*.sql`**

- **`POST /api/meters/reading`** accepts optional **`firmware_version`** and stores it on **`prototype_meter_state`** (nullable; only overwrites when a non-empty value is sent).
- **Check Meters** (`/om-report/check-meter-comparison`) and **XLSX summary** include **`firmware_version`** when present.

## Canary in flight (2026-04-17)

| Item | Value |
|------|-------|
| Release dir (build host) | `/opt/1meter-firmware/releases/fw-version-publish-20260418093241-e7d8e16` |
| App version | **1.1.0** |
| S3 prefix | `s3://1pwr-ota-firmware/firmware-releases/v1.1.0/fw-version-publish-20260418093241-e7d8e16/` |
| Signing profile | `1PWR_OTA_ESP32_v2` |
| Role | `arn:aws:iam::758201218523:role/1pwr-ota-service-role` |
| OTA update id | `1meter-v1-1-0-canary-OneMeter13-20260418094036` |
| IoT job id | `AFR_OTA-1meter-v1-1-0-canary-OneMeter13-20260418094036` |
| Target | `OneMeter13` (1M `23022673` → `0045MAK`) |
| Prior 1.0.8 job on OneMeter13 | **CANCELED** |

Once this canary **SUCCEEDS** and we see **`FirmwareVersion: 1.1.0`** on the `prototype_meter_state` row for `23022673`, roll to the full MAK fleet (next section).

## 4) Build, publish, fleet OTA (AWS `us-east-1`)

Use the remote build host and scripts (see archived **`docs/archive/2026-03-worktree-cleanup/1meter/1Meter-Remote-Build-OTA-Runbook.md`**) or local equivalents:

1. Build with **`OTA_APP_VERSION`** set to the new release.
2. **`publish_release.sh`** → **`1pwr-ota-firmware`**.
3. **`create_ota_update.sh`** with fleet target, e.g. **`THING_GROUP_NAMES=MAK_V1_0_2`** (or the group that contains all field devices — confirm in IoT console).
4. Monitor **IoT Jobs** until things reach **SUCCEEDED** (gateway/repeater ordering may still apply — see field SOP).

**Constraint:** OTA version must be **strictly greater** than the running image on each device.

## 5) Validate after rollout

- **DynamoDB / MQTT**: payload contains `firmware_version`.
- **CC DB**: `SELECT meter_id, firmware_version, last_seen_at FROM prototype_meter_state ORDER BY meter_id;`
- **Portal**: **Check Meters** → per-pair **FW** line when reported.

## Order of operations (recommended)

1. Ship **firmware** that publishes version (test on one Thing / bench).
2. Ship **Lambda** to forward `firmware_version`.
3. Deploy **CC** backend + run migration **`012`** (CI deploy does this on push to `main`).
4. Run **OTA** to the fleet (or canary first).

Skipping (1)–(2) and only deploying CC stores **NULL** until devices send the field.
