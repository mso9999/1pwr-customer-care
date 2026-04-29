# 1Meter v1.1.6 field-flash plan

> **TL;DR.** v1.1.1 OTA is structurally broken on the MAK mesh. The cause
> is a `case` fall-through in `ota_over_mqtt_demo.c::processOTAEvents()`
> `OtaAgentEventResume` handler — every MQTT reconnect during a download
> overwrites the "request next block" event with "activate image", so the
> agent keeps trying to activate the partial image and never converges.
> Fix is in v1.1.6 (`onepwr-aws-mesh@06085f5`). Because the bug lives in
> the agent ON the device, **v1.1.1 cannot OTA itself to v1.1.6** — the
> bug runs first. The MAK fleet must be serial-flashed by the field team.

## What changed in v1.1.6

Single-line bug fix plus a throughput knob:

1. **`OtaAgentEventResume` fall-through fix** — added the missing `break`
   after `RequestFileBlock`. The compiler had been emitting
   `[-Wimplicit-fallthrough=]` on every build of this file; we missed it.
2. **`NUM_OF_BLOCKS_REQUESTED` 1 → 4.** Per-block GetStream round-trip
   was the dominating cost. 4 quadruples download throughput, well within
   the OTA buffer pool of 5. Pre-fix: ~1.5 blocks/sec. Post-fix
   expectation: ~6 blocks/sec → 277-block image converges in ~45 s,
   inside the ~95 s mean-time-between-disconnect we've been measuring.
3. Carries forward all v1.1.4 changes: progress-publishing every 16
   blocks, periodic-restart suspension during OTA.

The diag-only flag (`CONFIG_GRI_DIAG_BUILD`) is `n` in v1.1.6 — full
firmware with meter / Modbus / relay-cmd subscriber back in.

## Evidence the fix is the right one

12 hours of overnight data from the v1.1.5 canary on OneMeter13:

- 60,000 stream events (Publish-Out blocks).
- ~440 MQTT disconnects (every ~95 s).
- Image is 277 blocks. ⇒ **216× oversampling, never converged.**

That ratio is impossible if `currentBlockOffset` were truly preserved
across reconnects; the only consistent explanation is that the resume
handler isn't actually advancing the download. Reading the resume
handler confirmed the fall-through.

## Artifacts

```text
s3://1pwr-ota-firmware/firmware-releases/v1.1.6/resume-fix-mak-20260429051610-e7d8e16/
  ├─ FeaturedFreeRTOSIoTIntegration.bin   (1132400 B, app)
  ├─ bootloader.bin
  ├─ partition-table.bin
  ├─ ota_data_initial.bin
  ├─ flasher_args.json
  ├─ release-manifest.json
  └─ sdkconfig
```

`release-manifest.json` confirms `router_ssid: "MAK_Wifi-ext"`,
`router_password_set: true`, `ota_app_version: "1.1.6"`. S3 VersionId
of the app binary: `vIhvu.ty.xvtC0CzcYRqfoSnfd_ylbAZ`.

## Field-flash procedure

For each MAK fleet device (8 customer check meters + 2 infra):

1. **Pull the bundle to the field laptop** (one-time):

   ```bash
   mkdir -p ~/1meter-fw-v1.1.6
   aws s3 sync \
     s3://1pwr-ota-firmware/firmware-releases/v1.1.6/resume-fix-mak-20260429051610-e7d8e16/ \
     ~/1meter-fw-v1.1.6/
   ```

2. **Connect the ESP32-C3 over USB**; identify the serial port (e.g.
   `/dev/ttyUSB0` on Linux, `/dev/cu.usbserial-XXXX` on macOS).

3. **Flash** (full image — bootloader + partition table + OTA data + app):

   ```bash
   cd ~/1meter-fw-v1.1.6
   python -m esptool --chip esp32c3 -p <PORT> -b 460800 \
     --before default_reset --after hard_reset \
     write_flash --flash_mode dio --flash_size 4MB --flash_freq 80m \
       0x0      bootloader.bin \
       0xb000   partition-table.bin \
       0x19000  ota_data_initial.bin \
       0x20000  FeaturedFreeRTOSIoTIntegration.bin
   ```

4. **Verify v1.1.6 booted** by watching for the next MQTT telemetry
   sample — it should report `"FirmwareVersion": "1.1.6"`:

   ```bash
   aws dynamodb query --table-name 1meter_data --region us-east-1 \
     --key-condition-expression 'device_id = :d' \
     --expression-attribute-values '{":d":{"S":"<12-digit serial>"}}' \
     --no-scan-index-forward --limit 1 --output json | \
     python3 -c 'import sys,json;
   it=json.load(sys.stdin)["Items"][0];
   print("FW:", it.get("FirmwareVersion",{}).get("S"));
   print("ts:", it.get("sample_time",{}).get("S"))'
   ```

5. **Update the AWS IoT Thing attributes** so the trust inventory and
   `MAK_V1_1_6` thing group can target only-v1.1.6 devices:

   ```bash
   aws iot update-thing --thing-name OneMeterXX --region us-east-1 \
     --attribute-payload '{"attributes":{"firmware_tag":"v1.1.6","reflashed_at":"2026-04-29"},"merge":true}'
   ```

6. **Move the Thing into a `MAK_V1_1_6` group** (create if missing):

   ```bash
   aws iot create-thing-group --thing-group-name MAK_V1_1_6 --region us-east-1 2>/dev/null || true
   aws iot add-thing-to-thing-group \
     --thing-group-name MAK_V1_1_6 --thing-name OneMeterXX --region us-east-1
   aws iot remove-thing-from-thing-group \
     --thing-group-name MAK_V1_1_1 --thing-name OneMeterXX --region us-east-1
   ```

## Validation: confirm OTA actually works once a device is on v1.1.6

After at least one device is on v1.1.6, push a no-op OTA targeting it
to confirm the agent's resume handler now behaves correctly:

```bash
# Re-build any minor variant (e.g. bump BUILD to 7), upload to S3, then:
aws iot create-ota-update --ota-update-id 1m-v1-1-7-resume-fix-validate \
  --targets arn:aws:iot:us-east-1:758201218523:thing/<thing> \
  --target-selection SNAPSHOT \
  --files file:///tmp/ota-files-117.json \
  --role-arn arn:aws:iam::758201218523:role/1pwr-ota-service-role \
  --protocols MQTT --region us-east-1
```

Watch:

```bash
aws iot describe-job-execution --job-id AFR_OTA-1m-v1-1-7-resume-fix-validate \
  --thing-name <thing> --region us-east-1
```

Expected behaviour with v1.1.6 on-device:

- `lastUpdatedAt` ticks every ~10 s with `statusDetails` showing
  `blocks_received`, `blocks_total`, `percent`, `bytes_received`
  (the v1.1.4 progress publish, called every 16 blocks).
- `status: SUCCEEDED` within a few minutes of connected time even
  with the current ~95 s disconnect cadence, because each reconnect
  resumes from `currentBlockOffset` instead of activating-the-partial.
- The device reboots once on activation and the next telemetry sample
  reports the new version.

If the canary above is green, schedule a thing-group OTA against
`MAK_V1_1_6` for the next firmware revision. Future fleet updates
won't need a field visit.

## Coordinating with the existing fleet monitor

The 1Meter fleet monitor (`scripts/ops/monitor_1meter_offline.py`)
will see brief offline windows on each device during the reflash
(power off → wait → reboot → reconnect). To suppress the resulting
WhatsApp recovery alert spam, either:

- Notify the team in advance and live with one alert per device, OR
- Temporarily disable the monitor during the field visit:

  ```bash
  ssh cc-host 'sudo systemctl stop cc-1meter-monitor.timer'
  # ... do the field flashes ...
  ssh cc-host 'sudo systemctl start cc-1meter-monitor.timer'
  ```

## Sequence of priority

The v1.1.6 reflash also unblocks the [Phase 2 of the billing migration
protocol](./1meter-billing-migration-protocol.md): Phase 2 needs the
`oneMeter/<thing>/cmd/relay` subscriber that shipped in v1.1.2 and the
relay-cmd payload format that's been stable since v1.1.4. v1.1.6 has
all of that. After the field visit, the Phase 2 enablement work
(currently held by `RELAY_AUTO_TRIGGER_ENABLED=0` and
`GUARD_CREDIT_ENABLED=0` env flags on the CC host) can be flipped on.
