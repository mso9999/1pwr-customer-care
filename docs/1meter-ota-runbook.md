# 1Meter remote build, publish, and OTA (active)

This is the **supported** location for 1Meter OTA tooling in this repo. Historical copies may exist under `docs/archive/2026-03-worktree-cleanup/1meter/`.

## Scripts (in-repo)

| Script | Purpose |
|--------|---------|
| `scripts/1meter/bootstrap_build_host.sh` | Bootstrap ESP-IDF / AWS CLI on a build host |
| `scripts/1meter/build_firmware_remote.sh` | Build a release bundle under `BASE_DIR/releases/` |
| `scripts/1meter/publish_release.sh` | Upload artifacts to S3, write `s3-publish-manifest.json` (with **VersionId**) |
| `scripts/1meter/create_ota_update.sh` | Create AWS IoT OTA update from the publish manifest |
| `scripts/1meter/onepwr-aws-mesh-timeout.patch` | Optional connectivity patch (see firmware repo history) |

Copy these to `/opt/1meter-firmware/scripts/` on the build host if you want paths to match older notes, or set `BASE_DIR` / run from any checkout.

## AWS resources (non-secret identifiers)

| Item | Value |
|------|--------|
| Account | `758201218523` |
| Region | `us-east-1` (IoT OTA / signing) |
| OTA bucket | `1pwr-ota-firmware` |
| OTA service role | `arn:aws:iam::758201218523:role/1pwr-ota-service-role` |
| Signing profile (active) | `1PWR_OTA_ESP32_v2` |
| MAK thing group (typical rollout) | `MAK_V1_0_2` |

**Credentials:** Use org IAM / SSO or an EC2 instance profile. Do not commit access keys. See `docs/credentials-and-secrets.md`.

## Build host

Firmware is built on the dedicated host (ESP-IDF **v5.2.3**), not in this CI workspace. See `CONTEXT.md` → **1Meter Firmware Build Host** for host IP, port, and paths (`/opt/1meter-firmware`).

## Anti-rollback rule

The OTA **app version baked into the image** must be **strictly higher** than the version on the device, or the device rejects the update. Before rolling the MAK fleet, confirm the highest version already deployed (e.g. from prior jobs / field report) and bump accordingly.

## Typical flow

1. **SSH to build host** → run `build_firmware_remote.sh` with `OTA_APP_VERSION` set above the current fleet line.
2. **Publish** with `publish_release.sh` (`S3_BUCKET=1pwr-ota-firmware`, `AWS_REGION=us-east-1`) from a context that has AWS credentials.
3. **Create OTA** with `create_ota_update.sh`, setting `SIGNING_PROFILE_NAME`, `OTA_ROLE_ARN`, and either:
   - `THING_NAMES=OneMeter18,OneMeter16,...` (comma-separated), or
   - `THING_GROUP_NAMES=MAK_V1_0_2` for a group rollout.

Use `DRY_RUN=1` on publish/OTA scripts to validate plans without calling AWS.

## MAK prototype fleet (reference — confirm in AWS IoT)

IoT **Thing** names use `OneMeter*` (Pascal case). Serial ↔ account context is in `CONTEXT.md` (Prototype 1Meters). Exclude devices that are already on the target firmware from `THING_NAMES`, or use explicit names only.
