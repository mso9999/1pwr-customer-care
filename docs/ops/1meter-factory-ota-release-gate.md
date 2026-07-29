# 1Meter factory-boot OTA release gate

## Current production finding (2026-07-29)

Factory units are documented as v1.1.56. The newest full application currently
found in `s3://1pwr-ota-firmware/firmware-releases/` is v1.1.53 and is organized
as per-Thing builds. It is older than the factory image and is not an approved
factory-promotion target.

Because the firmware enforces OTA anti-rollback, CC must remain fail-closed until
an approved full application with an OTA version strictly above 1.1.56 is built,
published with an immutable S3 VersionId, and canary-tested.

Do not configure CC to use a v1.1.53 artifact for the factory fleet.

## Starlink credential model

Each deployment site has its own Starlink Wi-Fi network.

The operator must enter the destination site’s exact Starlink SSID/password in
the provisioning-station UI. The station sends them in the local bootstrap
before CC schedules OTA. The gateway stores them in runtime NVS, reboots onto
Starlink, connects to AWS IoT, and receives the OTA Job.

CC stores:

- canonical site;
- Starlink SSID;
- Wi-Fi configuration version and timestamp;
- approved target firmware version;
- immutable S3 artifact version;
- OTA update ID and per-Thing status;
- installed firmware version once the Job is `SUCCEEDED`.

CC does not store or redisplay the Starlink password.

Runtime Wi-Fi configuration survives an application OTA. A build may carry a
non-secret site fallback SSID for recovery/provenance, but it must not overwrite
valid runtime Starlink credentials on each boot.

## Release production procedure

1. Start from reviewed firmware source; do not release from a dirty working tree.
2. Set the OTA application version strictly above the highest factory version
   (minimum next candidate: 1.1.57).
3. Build the full operational application with OTA enabled.
4. Confirm the application:
   - fits the OTA partition;
   - trusts signing profile `1PWR_OTA_ESP32_v2`;
   - loads runtime identity/TLS/Wi-Fi from NVS;
   - does not reset the runtime Starlink credentials on reboot;
   - preserves the provisioning registry/Thing identity across OTA.
5. Publish the application binary to the versioned
   `1pwr-ota-firmware` bucket and record its immutable S3 VersionId.
6. Run the signed OTA against one controlled gateway first.
7. Confirm:
   - AWS OTA creation is complete;
   - the per-Thing Job is `SUCCEEDED`;
   - the gateway reconnects through its Starlink network;
   - telemetry reports the target version;
   - meter acquisition/normal operational tasks work.
8. Soak the canary for the agreed period.
9. Approve the release per site in CC configuration.
10. Only then enable a field/depot batch.

## CC release configuration

For one universal approved release:

```text
ONEMETER_OTA_APP_KEY=firmware-releases/v1.1.57/<release>/FeaturedFreeRTOSIoTIntegration.bin
ONEMETER_OTA_APP_VERSION_ID=<immutable-s3-version-id>
ONEMETER_OTA_TARGET_VERSION=1.1.57
ONEMETER_FACTORY_BASELINE_VERSION=1.1.56
```

For per-site release tracking, use `ONEMETER_OTA_RELEASES_JSON`. Example
(placeholders only):

```json
{
  "GBO": {
    "artifact_key": "firmware-releases/v1.1.57/GBO/FeaturedFreeRTOSIoTIntegration.bin",
    "artifact_version_id": "<immutable-s3-version-id>",
    "target_firmware_version": "1.1.57",
    "factory_baseline_version": "1.1.56",
    "credentials_mode": "runtime_nvs",
    "fallback_ssid": "<optional non-secret fallback SSID>"
  },
  "SAM": {
    "artifact_key": "firmware-releases/v1.1.57/SAM/FeaturedFreeRTOSIoTIntegration.bin",
    "artifact_version_id": "<immutable-s3-version-id>",
    "target_firmware_version": "1.1.57",
    "factory_baseline_version": "1.1.56",
    "credentials_mode": "runtime_nvs",
    "fallback_ssid": "<optional non-secret fallback SSID>"
  }
}
```

Do not put Starlink passwords in environment variables, source control, build
manifests, release metadata, or operator documentation.

## CC host IAM

The inline role policy `cc-postgres-backup-role/cc-1meter-provisioning` was
extended on 2026-07-29 with:

- IoT OTA/Stream/Job creation and status read;
- object-version read limited to `1pwr-ota-firmware/*`;
- Signer actions limited to `1PWR_OTA_ESP32_v2`;
- `iam:PassRole` limited to `1pwr-ota-service-role` for the IoT service.

CC readiness still checks the selected artifact and signing profile at runtime.
Permission alone does not make a release approved.

## Release decision

The field UI must show **not ready** if:

- no site/release is selected;
- the S3 key or immutable VersionId is missing;
- the target version is not set;
- the target version is not valid `MAJOR.MINOR.BUILD` or is not strictly newer
  than the configured factory baseline;
- S3 cannot read that exact version;
- the signing profile check fails.

An identity bootstrap can technically succeed while OTA is unavailable. The
operator must treat that as incomplete provisioning and must not release the
gateway.
