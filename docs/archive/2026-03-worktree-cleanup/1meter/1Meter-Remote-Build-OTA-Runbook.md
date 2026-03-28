# 1Meter Remote Build and OTA Runbook

## Current State

- Remote firmware build host:
  - host: `13.247.190.132`
  - SSH: `ssh -p 2222 -i uGridPLAN.pem ubuntu@13.247.190.132`
  - workspace: `/opt/1meter-firmware`
- Proven working on the host:
  - native ESP-IDF `v5.2.3` build
  - release artifact generation under `/opt/1meter-firmware/releases`
  - AWS CLI v2 installed
- Current gap on the host:
  - `aws` is installed, but the host has no AWS credentials or instance role yet

## Known AWS Resources

- AWS account: `758201218523`
- OTA firmware bucket: `1pwr-ota-firmware`
- OTA service role: `arn:aws:iam::758201218523:role/1pwr-ota-service-role`
- Active signing profiles seen locally:
  - `1PWR_OTA_ESP32_v2`
  - `MAK_OTA_Profile`
  - `ESP32C3PROFILE`
- Existing thing groups seen locally:
  - `ESP32C3-TEST`
  - `MAK_V1_0_2`

## Helper Scripts

- `scripts/1meter/bootstrap_build_host.sh`
- `scripts/1meter/build_firmware_remote.sh`
- `scripts/1meter/publish_release.sh`
- `scripts/1meter/create_ota_update.sh`

## 1. Build a Release on the Staging Host

From the local machine:

```bash
ssh -p 2222 -i "/path/to/uGridPLAN.pem" ubuntu@13.247.190.132 \
  'bash -lc '"'"'
    export ALLOW_DIRTY=1
    export OTA_APP_VERSION=1.0.1
    export BUILD_LABEL=timeout-patch-$(date -u +%Y%m%d%H%M%S)
    /opt/1meter-firmware/scripts/build_firmware_remote.sh
  '"'"''
```

Notes:

- `ALLOW_DIRTY=1` is currently needed because the remote clone has the timeout patch applied locally and not yet committed upstream.
- `OTA_APP_VERSION` is optional, but should be set for any real OTA candidate so the image version is strictly higher than the deployed version.
- The release directory is printed at the end and stored under `/opt/1meter-firmware/releases/`.
- The generated `release-manifest.json` now includes:
  - project name
  - project version
  - target
  - OTA app version
  - app binary name

## 2. Publish a Release to S3

Once the build host has AWS auth, publish from the host:

```bash
ssh -p 2222 -i "/path/to/uGridPLAN.pem" ubuntu@13.247.190.132 \
  'bash -lc '"'"'
    export RELEASE_DIR=/opt/1meter-firmware/releases/<release-dir>
    export S3_BUCKET=1pwr-ota-firmware
    export AWS_REGION=us-east-1
    /opt/1meter-firmware/scripts/publish_release.sh
  '"'"''
```

Behavior:

- uploads only the release artifacts needed for flashing / OTA by default
- does **not** upload `sdkconfig` unless `INCLUDE_SDKCONFIG=1`
- writes `s3-publish-manifest.json` in the release directory for the OTA step
- records the S3 object `VersionId` values required by `aws iot create-ota-update`

Dry-run planning:

```bash
export DRY_RUN=1
/opt/1meter-firmware/scripts/publish_release.sh
```

## 3. Create an OTA Update

Use the publish manifest from step 2:

```bash
ssh -p 2222 -i "/path/to/uGridPLAN.pem" ubuntu@13.247.190.132 \
  'bash -lc '"'"'
    export RELEASE_DIR=/opt/1meter-firmware/releases/<release-dir>
    export SIGNING_PROFILE_NAME=1PWR_OTA_ESP32_v2
    export OTA_ROLE_ARN=arn:aws:iam::758201218523:role/1pwr-ota-service-role
    export THING_NAMES=OneMeter14
    export AWS_REGION=us-east-1
    /opt/1meter-firmware/scripts/create_ota_update.sh
  '"'"''
```

Alternative targets:

- single canary group: `THING_GROUP_NAMES=ESP32C3-TEST`
- site rollout group: `THING_GROUP_NAMES=MAK_V1_0_2`
- explicit ARN targets: `TARGET_ARNS=arn:aws:iot:...`

Dry-run planning:

```bash
export DRY_RUN=1
export ACCOUNT_ID=758201218523
/opt/1meter-firmware/scripts/create_ota_update.sh
```

## 4. Important OTA Constraint

The OTA version in firmware must be **strictly higher** than the version already running on the target device, or the device will reject the image during anti-rollback checks.

Before a real canary release:

1. bump the OTA app version in firmware
2. rebuild
3. publish the new release
4. create the OTA update

## Recommended Next Infra Step

To make this fully remote-first, give the staging host AWS access without storing a personal key on disk.

Preferred option:

- attach an EC2 instance profile with least-privilege access for:
  - `s3:PutObject`, `s3:GetObject`, `s3:GetObjectVersion`
  - `iot:CreateOTAUpdate`, `iot:GetOTAUpdate`, `iot:ListOTAUpdates`
  - `signer:StartSigningJob`, `signer:DescribeSigningJob`, `signer:GetSigningProfile`

Fallback option:

- run `aws configure` or `aws login` on the staging host

## Practical Canary Recommendation

- Build a new release with a bumped OTA version.
- Publish it to `1pwr-ota-firmware`.
- Create a canary OTA update for one known-good target first, preferably a bench/test thing or `ESP32C3-TEST`.
- Only after success should the rollout expand to `MAK_V1_0_2` or individual field devices.
