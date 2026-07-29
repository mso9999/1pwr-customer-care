# 1Meter factory-boot OTA release gate

## Current production finding (2026-07-29)

The factory-installed archive has now been identified and verified:

- archive: `1meter-fw-v1.1.56-OEM-factory(1).zip`;
- application SHA-256:
  `63adecc88282118b28cd6c97d6dbf0eeb5e986b048f871c6f0339f104b91ea45`;
- source: CI run `28239462541`, commit `e4f9b46`, `site_config=FACTORY`;
- the binary is byte-for-byte identical to the preserved repository copy.

### Important architecture finding

The shipped v1.1.56 image behaves like a bootstrap before provisioning, but it
is not a stripped commissioning-only binary. It contains meter/RS485, mesh,
MQTT, OTA, diagnostics, relay command, and publish-timer code. Before runtime
identity/TLS exists it suppresses the MQTT manager; after CC writes runtime
identity/TLS/Wi-Fi and the unit reboots, the same binary enables its operational
cloud path.

An earlier, genuinely stripped commissioning build was implemented in March
2026 in the disposable clone `/tmp/onepwr-aws-mesh-review` using
`CONFIG_ONEPWR_COMMISSIONING_BUILD`. That work was not merged into firmware
`main`, its package is no longer in the repository, and its required
factory-to-full bench canary remained unexecuted. See
`onepwr-aws-mesh/Docs/migrated-from-cc/SESSION_LOG_1M_extract_from_CC.md`
(session 2026-03-28 and follow-ups).

The June HQ test that cleared v1.1.56 proved the web/bootstrap flow: discovery,
runtime identity/TLS/Wi-Fi delivery, reboot, cloud connection, reconciliation,
and rename. It did not install a second application image. The prior successful
v1.1.53 AWS OTA canary was performed on an already operational gateway, not on
the stripped commissioning build.

### Current candidate and remaining blocker

A no-functional-delta v1.1.57 candidate was built from firmware commit
`6ea321048c8fc23564e5d9de91fccc1d821162ae` by GitHub Actions run
`30465978595`. The build, binary sanity check, workflow artifact, and S3
publication succeeded. No OTA Job was created by the release workflow.

The immutable candidate application is:

- bucket: `1pwr-ota-firmware`;
- key:
  `firmware-releases/v1.1.57/HQTEST-1/FeaturedFreeRTOSIoTIntegration.bin`;
- S3 VersionId: `Xmr6xlSeZrdyVDZX1hm3zXtuREbcECGl`;
- size: `1,189,920` bytes;
- ETag: `308b287a13c82d8d11c01e72c333d0aa`;
- signing profile: `1PWR_OTA_ESP32_v2` (Active);
- OTA service role: `arn:aws:iam::758201218523:role/1pwr-ota-service-role`.

This is a **candidate only**, not an approved field release. The remaining
blocker is physical proof on one controlled gateway carrying the exact OEM
factory v1.1.56 image. The operator must power and identify that unit, provision
it as an explicitly authorized test Thing, then use CC's **OTA canary** tab.
Do not reuse a field gateway or either known older v1.1.53 HQ test gateway.

The device OTA code logs `MAJOR.MINOR.BUILD` but does not compare the running
semantic version with the job's `fileVersion`. The strict
`target > factory_baseline` check is a CC release-safety policy, not a firmware
anti-rollback mechanism. Keep that policy: the next approved target should be
v1.1.57 or later so installed state and release provenance remain unambiguous.

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
   (minimum next candidate: 1.1.57). This is the CC release policy; the current
   device agent does not provide semantic anti-rollback.
3. Build the full operational application with OTA enabled.
4. Confirm the application:
   - fits the OTA partition;
   - trusts signing profile `1PWR_OTA_ESP32_v2`;
   - loads runtime identity/TLS/Wi-Fi from NVS;
   - does not reset the runtime Starlink credentials on reboot;
   - preserves the provisioning registry/Thing identity across OTA.
5. Publish the application binary to the versioned
   `1pwr-ota-firmware` bucket and record its immutable S3 VersionId.
6. Run the signed OTA against one controlled unit carrying the exact v1.1.56
   OEM factory image first. Provision its runtime identity/TLS/Wi-Fi through
   the same CC/station flow that will be used in Benin; do not substitute an
   already operational legacy gateway for this acceptance test.
7. Confirm:
   - AWS OTA creation is complete;
   - the per-Thing Job is `SUCCEEDED`;
   - the gateway reconnects through its Starlink network;
   - telemetry reports the target version;
   - meter acquisition/normal operational tasks work.
   - runtime Thing identity, TLS material, and Starlink Wi-Fi remain intact
     across the OTA reboot.
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
    "artifact_key": "firmware-releases/v1.1.57/HQTEST-1/FeaturedFreeRTOSIoTIntegration.bin",
    "artifact_version_id": "Xmr6xlSeZrdyVDZX1hm3zXtuREbcECGl",
    "target_firmware_version": "1.1.57",
    "factory_baseline_version": "1.1.56",
    "credentials_mode": "runtime_nvs",
    "fallback_ssid": null,
    "canary_only": true
  },
  "SAM": {
    "artifact_key": "firmware-releases/v1.1.57/HQTEST-1/FeaturedFreeRTOSIoTIntegration.bin",
    "artifact_version_id": "Xmr6xlSeZrdyVDZX1hm3zXtuREbcECGl",
    "target_firmware_version": "1.1.57",
    "factory_baseline_version": "1.1.56",
    "credentials_mode": "runtime_nvs",
    "fallback_ssid": null,
    "canary_only": true
  }
}
```

Also set `ONEMETER_OTA_CANARY_THINGS` only after the controlled factory unit has
been allocated a test Thing. Keep `RELAY_AUTO_TRIGGER_ENABLED=0` until the OTA
canary and the protected dummy-load disconnect/payment/reconnect validation
both pass.

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
gateway. Comfort should not proceed past the release-readiness gate until the
factory-image canary above has passed and the approved release is configured.
