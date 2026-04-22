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
| `OneMeter14, 15, 16, 17, 18, 11, 5` | various | various MAK | **presumed** `03:9E:44:...` (v2) | pre-1.1.1 | original provisioning; field team confirms no post-provisioning cert changes by them | pending |
| `OneMeter19` (`23022684`) | 0051MAK | 0051MAK | unknown — recently reassigned | — | prototype/bench before 0051MAK install | pending |
| `OneMeter44` (`23022667`) | (gateway) | — | presumed v2 | pre-1.1.1 | original provisioning | pending |
| `OneMeter122` (`23021886`) | 0056MAK | 0056MAK | unknown — "Schyler batch" PCBs per field team (flashed locally) | — | may have different embedded cert | **needs check** |
| `OneMeter12` (`23021888`) | 0058MAK | 0058MAK | unknown — "Schyler batch" | — | same as above | **needs check** |

Field team note (2026-04-21, Motlatsi): the Schyler batch (roughly 7 PCBs incl. one `ExampleThing`) was the only one the field team touched post-provisioning; the other MAK units were flashed centrally and should carry cert v2.

## Timeline of this issue (for posterity)

| Date | Event |
|------|-------|
| 2026-02-20 | `1PWR_OTA_ESP32_v2` signing profile + matching ACM cert `03:9E:44:...` created. |
| 2026-02-21 | Fleet batch flashed with v2 cert embedded. |
| 2026-03-10 | `v1.0.2-MAKGroup` OTA: **1 SUCCEEDED (OneMeter6), 7 CANCELED**. Root cause of the cancels was never fully diagnosed — in hindsight likely a policy/permissions issue rather than cert, because all devices were on v2 at that time. |
| ~2026-04 | `aws_codesign.crt` in `onepwr-aws-mesh` gets overwritten with a new cert (`18:92:8E:...`) whose private key is not in any AWS signing profile. Source of this commit is unclear; may have been an exploratory key rotation by a developer. No new Signer profile created; no fleet reflash. **Orphan cert is now embedded in any new build.** |
| 2026-04-18 | v1.1.0 canary OTA to `OneMeter13` signed with `1PWR_OTA_ESP32_v2` — stuck IN_PROGRESS silently. Diagnosed as signature rejection because the **orphan cert** was in the new build, not the v2 cert the device expects. |
| 2026-04-20 | v1.1.0 canary retried under `MAK_OTA_Profile` (v1 cert) — same stuck pattern. |
| 2026-04-21 | Field team confirms only the Schyler batch (non-deployed) had cert changes, so the deployed fleet is on v2. MSO confirms the v2 profile is legit ours. |
| 2026-04-22 | **This fix.** `aws_codesign.crt` reverted in the build-host repo to the real v2 cert (`03:9E:44:...`). Version bumped to 1.1.1 (anti-rollback). Rebuilt; uploaded; OTA signed with `1PWR_OTA_ESP32_v2`. Trust inventory doc created. |

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
