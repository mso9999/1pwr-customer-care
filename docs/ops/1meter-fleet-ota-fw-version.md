# Fleet OTA + firmware version visibility (1Meter)

End state: every device runs a **new** build that **publishes** its version string on the normal telemetry path, **`ingestion_gate`** forwards it to CC, and **`prototype_meter_state.firmware_version`** + **Check Meters** show it.

## 1) Firmware (`onepowerLS/onepwr-aws-mesh`)

- Add a stable field to the **MQTT JSON** (and/or the payload the Lambda forwards), e.g. **`firmware_version`**: semver or `OTA_APP_VERSION` / `PROJECT_VER` string from build.
- Ensure the value is present **on every publish** (or at least on boot + hourly) so CC updates even when energy delta is 0.
- Bump **OTA app version** so the image is **strictly higher** than every device (anti-rollback).

## 2) Ingestion (`onepowerLS/ingestion_gate` Lambda)

- Map the MQTT key into the POST body to **`https://cc.1pwrafrica.com/api/meters/reading`** (or regional CC URL) as optional **`firmware_version`** (string).
- No change to `X-IoT-Key` contract.

## 3) Customer Care API (this repo) — deployed with migration **`012_*.sql`**

- **`POST /api/meters/reading`** accepts optional **`firmware_version`** and stores it on **`prototype_meter_state`** (nullable; only overwrites when a non-empty value is sent).
- **Check Meters** (`/om-report/check-meter-comparison`) and **XLSX summary** include **`firmware_version`** when present.

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
