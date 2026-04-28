# 1Meter OTA trust inventory

**Single source of truth** for the code-signing certificate + signing profile + firmware cert embed used to sign and verify OTA updates for the 1Meter fleet. Any drift between these three fields = the fleet can no longer be OTA'd.

**Rule 1:** Never commit a new `onepwr-aws-mesh/main/certs/aws_codesign.crt` without:

1. Either pointing at an **existing** signing profile whose ACM cert has a matching SHA1 fingerprint (see table below), **or** creating a new matching AWS Signer profile + ACM cert **in the same PR**.
2. Updating this inventory.
3. Planning the re-flash (physical) of any fleet members that carry the old cert — OTAs to those devices with the new key will fail silently.

**Rule 2:** When a device is flashed (factory, field, or OTA), record its embedded cert fingerprint as an AWS IoT Thing attribute `cert_fp` plus `firmware_tag`:

```bash
aws iot update-thing --thing-name OneMeter13 --region us-east-1 \
  --attribute-payload '{"attributes":{"cert_fp":"03:9E:44:43:40:A2:85:A5:58:50:40:AC:FE:5B:8A:56:0C:5B:3E:A1","firmware_tag":"v1.1.1"},"merge":true}'
```

This makes `aws iot search-index 'attributes.cert_fp:03:9E:*'` the authoritative per-device inventory, surviving SESSION_LOG / repo churn.

---

## Key pairs

| Key pair name | ACM cert ARN | SHA1 fingerprint | Subject | NotAfter | AWS Signer profile | Status |
|---------------|--------------|------------------|---------|----------|--------------------|--------|
| **v2** (current) | `arn:aws:acm:us-east-1:758201218523:certificate/53e8b57f-0d64-42a8-ba28-a9a9587b7f88` | `03:9E:44:43:40:A2:85:A5:58:50:40:AC:FE:5B:8A:56:0C:5B:3E:A1` | `CN=1PWR OTA Signer, O=OnePower, C=LS` | 2036-02-18 | **`1PWR_OTA_ESP32_v2`** | **Active — should be embedded in all field devices and in `aws_codesign.crt` at repo HEAD.** |
| **v1** (legacy) | *(ACM ARN TBD — attached to the two profiles below)* | `CB:98:92:F4:82:38:C6:26:98:6A:7E:CC:A2:3C:3B:75:E8:2B:C8:CD` | `CN=1PWR OTA Signer, O=OnePower, C=LS` | — | `MAK_OTA_Profile`, `ESP32C3PROFILE` | **Superseded** — profiles still exist in AWS but should not be used for new signing. Any surviving devices flashed with this cert need a physical re-flash to move onto v2. |
| **orphan-2026-04** | *(no ACM cert; private key unknown)* | `18:92:8E:D3:2F:EF:51:12:28:99:27:35:4F:9D:FC:DF:96:37:97:22` | `CN=1PWR OTA Signer, O=OnePower, C=LS` | 2036-02-19 | *(none)* | **Do not use.** Committed to `aws_codesign.crt` at some point (source lost), but no AWS Signer profile was ever created with the matching private key. Any firmware built with this cert embedded is un-OTA-able. Reverted 2026-04-22 (see below). |

## Field fleet cert provenance

Populate as devices are flashed or inspected. Initial entries are inferred from the March 2026 successful OTA (which used profile `1PWR_OTA_ESP32_v2` → v2 cert).

| Thing name | Short meter id | Account | Cert fingerprint | Firmware tag | Source | Verified? |
|------------|----------------|---------|------------------|--------------|--------|-----------|
| `OneMeter6` | (bench) | — | `03:9E:44:...` (v2) | v1.0.2 | March 2026 OTA success | ✔ (only successful OTA on record) |
| `OneMeter13` | 23022673 | 0045MAK | **presumed** `03:9E:44:...` (v2) | pre-1.1.1 | original provisioning | pending — OTA in flight 2026-04-22 |
| `OneMeter13` | 23022673 | 0045MAK | `03:9E:44:...` (v2) | **v1.1.3** (Phase 2 relay-cmd subscriber) | OTA `1m-v1-1-3-canary-OneMeter13-20260428163531`, 2026-04-28 — single-thing canary per field team's request | pending — verify via `prototype_meter_state.firmware_version='1.1.3'` once execution completes |
| `OneMeter11, 17, 18` | various | MAK check meters | `03:9E:44:...` (v2) | **v1.1.1** | (in `MAK_V1_1_1` thing group; will get the v1.1.3 group OTA after the OneMeter13 canary soaks) | already on v1.1.1; reflash to v1.1.3 pending canary success |
| `OneMeter5, 14, 15, 16` | various | various MAK | `03:9E:44:...` (v2) | **v1.1.1** | field team did the serial reflash post-2026-04-22 (per Motlatsi 2026-04-28 update); FW publish now visible in MQTT telemetry | confirmed v1.1.1 in DynamoDB; reflash to v1.1.3 pending canary success |
| `OneMeter19` (`23022684`) | 0051MAK | 0051MAK | unknown — recently reassigned | — | prototype/bench before 0051MAK install | pending |
| `OneMeter44` (`23022667`) | (gateway) | — | presumed v2 | pre-1.1.1 | original provisioning | pending |
| `OneMeter122` (`23021886`) | 0056MAK | 0056MAK | unknown — "Schyler batch" PCBs per field team (flashed locally) | — | may have different embedded cert | **needs check** |
| `OneMeter12` (`23021888`) | 0058MAK | 0058MAK | unknown — "Schyler batch" | — | same as above | **needs check** |

Field team note (2026-04-21, Motlatsi): the Schyler batch (roughly 7 PCBs incl. one `ExampleThing`) was the only one the field team touched post-provisioning; the other MAK units were flashed centrally and should carry cert v2.

## Timeline of this issue (for posterity)

| Date | Event |
|------|-------|
| 2026-02-20 | `1PWR_OTA_ESP32_v2` signing profile + matching ACM cert `03:9E:44:...` created. |
| 2026-02-21 | **Factory flash** of OneMeter5/11/13/14/15/16/17/18 for the MAK field deployment. At that commit (`6d68d97`) `main/certs/aws_codesign.crt` was **git-ignored / untracked** — a locally-generated ECDSA cert sat on the build laptop. **Private key of that cert was never checked in anywhere.** AWS Signer was configured to sign with the ACM cert (`03:9E:44:...`); devices verified against the local cert. **Cryptographic mismatch baked into the fleet.** |
| 2026-02-27 | Mistake spotted. Commit `90ac9ad` ("Fix OTA signature verification: embed correct ACM code signing certificate") committed the **ACM v2 cert** into the repo. Future builds are fine; already-flashed fleet is stuck because the Feb-21 local private key was disposed of. |
| ~2026-03-04 | Two follow-up commits (`0c6842f`, `170201a`) registered the cert with the OTA PAL and fixed a linker symbol — making OTA *potentially* work for devices flashed after this point. |
| 2026-03-10 | `v1.0.2-MAKGroup` OTA: **1 SUCCEEDED (OneMeter6), 7 CANCELED.** In hindsight: OneMeter6 was the one bench device re-flashed post-Feb-27 with the repo's new ACM cert. The 7 CANCELED devices are the factory-flashed MAK fleet still carrying the Feb-21 local cert — same silent signature-rejection we see today. |
| 2026-04-08 | `aws_codesign.crt` in `onepwr-aws-mesh` gets overwritten at commit `53ad310` ("v1.0.5: NVS energy persistence") with a new cert (`18:92:8E:...`) whose private key is not in any AWS signing profile. No new Signer profile created; no fleet reflash. **Orphan cert is now embedded in any new build** — but moot for the MAK fleet, which would have rejected ACM-signed v2 builds anyway. |
| 2026-04-18 → 2026-04-22 | Multiple OTA canary attempts on OneMeter13 (one with each AWS signing profile) — all stuck IN_PROGRESS with empty `statusDetails`, resume with old firmware. Confirms the Feb-21 cryptographic mismatch is the only root cause. |
| 2026-04-22 | **RCA complete.** Confirmed via git history of `aws_codesign.crt` and the `90ac9ad` commit message that the Feb-21 factory flash used a cert whose private key is unrecoverable. **MAK fleet must be physically re-flashed** to escape this. Repo realigned to v2 cert (`03:9E:44`) so future OTAs will work from any device serial-flashed with the current build. Stuck canary cancelled. |
| 2026-04-28 | **v1.1.2 OTA (Phase 2 relay-cmd subscriber)** built without `SITE_CONFIG` set on the build host, so it embedded the build-host default Wi-Fi creds (`DareMightyThings`/`bestcity`) instead of MAK's. App-side `app_wifi_init()` applies `CONFIG_ROUTER_SSID/PASSWORD` unconditionally on every boot — there is no NVS override path — so any device that completed the OTA would have failed to rejoin Wi-Fi after reboot. Field team flagged the SSID requirement before any device finished the download (job sat at `IN_PROGRESS` with no progress for ~3 h, likely IoT scheduler latency rather than cert mismatch). The `1m-v1-1-2-relay-cmd-20260428132839` OTA was cancelled and deleted; no fleet impact. |
| 2026-04-28 | **v1.1.3 rebuilt with `SITE_CONFIG=site-configs/MAK.conf`** (`onepwr-aws-mesh@f16ff3d`). `release-manifest.json` confirms `router_ssid: MAK_Wifi-ext`. Build-host script `build_firmware_remote.sh` patched with a fail-closed Wi-Fi-SSID guard that refuses to publish a release embedding `DareMightyThings` unless `ALLOW_DEFAULT_WIFI=1` is set; guard verified to fire correctly. Single-device canary OTA `1m-v1-1-3-canary-OneMeter13-20260428163531` created against OneMeter13 (23022673, 0045MAK) per field team's specific request. |

## Implication: the MAK fleet cannot be OTA'd until a field visit

`OneMeter6` is the only deployed device that can be OTA'd today because it's the only one with the ACM v2 cert baked in. All 8 MAK devices that went out in Feb must be **serial-flashed** (UART) with the current build (cert v2 + version ≥ 1.1.1) to rejoin the OTA-capable fleet.

**Minimum viable field procedure:**

1. Unbox a laptop with `idf.py` + this repo cloned at the latest main (build host or a colleague's).
2. `cd /opt/1meter-firmware/onepwr-aws-mesh && idf.py -p /dev/ttyUSB0 flash`.
3. Update the Thing attributes in AWS:

   ```bash
   aws iot update-thing --thing-name OneMeterXX --region us-east-1 \
     --attribute-payload '{"attributes":{"cert_fp":"03:9E:44:...","firmware_tag":"v1.1.1"},"merge":true}'
   ```
4. Confirm on MQTT: `FirmwareVersion: "1.1.1"` appears in the next telemetry sample.
5. Move Thing out of `MAK_V1_0_2` into a new `MAK_V1_1_1` group so the next OTA job can target only the re-flashed devices.

From that point on, OTA works for those devices and we're out of the latent cert-mismatch hole for good.

## Adding a new device (checklist)

1. Flash with the current build from `build_firmware_remote.sh` (which embeds `aws_codesign.crt` = v2 until this inventory says otherwise).
2. Register the Thing in AWS IoT.
3. Attach `DevicePolicy` + `ExampleThing-Policy` (or whatever the ops doc mandates).
4. Set attributes:

   ```bash
   aws iot update-thing --thing-name OneMeterN --region us-east-1 \
     --attribute-payload '{"attributes":{"cert_fp":"03:9E:44:...","firmware_tag":"vX.Y.Z"},"merge":true}'
   ```
5. Add the Thing to the `meters` table in 1PDB (`platform=prototype`, `role=check`, etc.) so it shows up on `/check-meters`.
6. Update the "Field fleet cert provenance" table above.

## Rotating the signing key (when it comes time)

Order matters:

1. Create new ACM cert + AWS Signer profile (`1PWR_OTA_ESP32_v3`).
2. Commit new `aws_codesign.crt` to `onepwr-aws-mesh/main/certs/` **and** update this inventory in the same PR.
3. Rebuild firmware (embedded cert = v3).
4. **Flash the new build onto every field device** (physical — `idf.py flash`). OTA cannot do this step because the old fleet's embedded cert is v2 and the new build is signed with v3 → each device would reject the update.
5. After every Thing has `attributes.cert_fp` = v3 fingerprint, deprecate the v2 profile (do not delete; keep for audit).

## Related files

- `onepwr-aws-mesh/main/certs/aws_codesign.crt` — the embedded cert; must match the Active key pair above.
- `onepwr-aws-mesh/main/main.c` — `_binary_aws_codesign_crt_start/end` link symbols.
- `onepwr-aws-mesh/main/CMakeLists.txt` — `target_add_binary_data(... certs/aws_codesign.crt TEXT)` line.
- `onepwr-aws-mesh/sdkconfig.defaults` — `CONFIG_GRI_OTA_DEMO_APP_VERSION_*` (must strictly increase across OTAs; ESP32 anti-rollback).
- `/opt/1meter-firmware/scripts/build_firmware_remote.sh` — build entry point on the build host.
- `/opt/1meter-firmware/scripts/create_ota_update.sh` — OTA job helper.
- `docs/ops/1meter-ota-investigation-2026-04-20.md` — investigation that surfaced this drift.
- Archived runbook: `docs/archive/2026-03-worktree-cleanup/1meter/1Meter-Remote-Build-OTA-Runbook.md`.
