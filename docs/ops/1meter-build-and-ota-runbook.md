# 1Meter firmware build + OTA runbook

> **Why this exists.** On 2026-04-28 a v1.1.2 OTA was created targeting the
> MAK fleet without `SITE_CONFIG` set; the resulting firmware embedded the
> build-host default Wi-Fi creds (`DareMightyThings` / `bestcity`) instead
> of MAK's (`MAK_Wifi-ext` / `1PWR_M@k123`). Field-deployed devices apply
> `CONFIG_ROUTER_SSID`/`CONFIG_ROUTER_PASSWORD` unconditionally on every
> boot via `app_wifi_init()` — there is no NVS override path. If any
> device had completed the OTA download, the post-reboot Wi-Fi join would
> have failed permanently and bricked the unit until a serial reflash.
> The OTA was cancelled before any device finished. This runbook captures
> the safe path so it can't happen again.

## Required SSH access

Build host is `13.247.190.132:2222`, user `ubuntu`, key `uGridPLAN.pem`
(in `Secrets/`). Confirm with `ssh -p 2222 -i .../uGridPLAN.pem
ubuntu@13.247.190.132 'whoami'`.

## Per-site Wi-Fi credentials

Each deployment site has a `*.conf` file under
`/opt/1meter-firmware/site-configs/` that overrides
`CONFIG_ROUTER_SSID` / `CONFIG_ROUTER_PASSWORD`. The build script
applies it after copying `sdkconfig.defaults` into place, so the
embedded creds always match the file you point at.

| Site | Override file (build host) | SSID |
|------|----------------------------|------|
| MAK | `/opt/1meter-firmware/site-configs/MAK.conf` | `MAK_Wifi-ext` |

Do **not** commit site creds to the `onepwr-aws-mesh` repo — they live
on the build host only. The repo's `sdkconfig.defaults` keeps a
non-deployable default (`DareMightyThings`) precisely so a forgotten
`SITE_CONFIG` argument trips the guard described below instead of
silently shipping wrong creds.

## Standard MAK build invocation

```bash
ssh -p 2222 -i .../uGridPLAN.pem ubuntu@13.247.190.132 '
  cd /opt/1meter-firmware && \
  ALLOW_DIRTY=1 \
  OTA_APP_VERSION=<MAJOR.MINOR.BUILD> \
  SITE_CONFIG=/opt/1meter-firmware/site-configs/MAK.conf \
  BUILD_LABEL=<descriptive-label>-$(date -u +%Y%m%d%H%M%S) \
    bash /opt/1meter-firmware/scripts/build_firmware_remote.sh
'
```

The build emits a release dir under `/opt/1meter-firmware/releases/`
containing `FeaturedFreeRTOSIoTIntegration.bin`, `bootloader.bin`,
`partition-table.bin`, `ota_data_initial.bin`, `sdkconfig`, and
`release-manifest.json`. The manifest now records `router_ssid` and
`router_password_set` — verify before publishing:

```bash
ssh ... 'cat /opt/1meter-firmware/releases/<release>/release-manifest.json'
# expected: "router_ssid": "MAK_Wifi-ext", "router_password_set": true
```

## WiFi-SSID guard

`scripts/ops/build_firmware_remote.sh` (mirrored from the build host)
appends a fail-closed guard that runs after every successful build:

```bash
EFFECTIVE_SSID="$(grep -E '^CONFIG_ROUTER_SSID=' "${RELEASE_DIR}/sdkconfig" | cut -d= -f2- | tr -d '"')"
echo "Embedded CONFIG_ROUTER_SSID: ${EFFECTIVE_SSID}"
if [[ "${EFFECTIVE_SSID}" == "DareMightyThings" && "${ALLOW_DEFAULT_WIFI:-0}" != "1" ]]; then
  echo "ERROR: build embeds the build-host default SSID 'DareMightyThings'." >&2
  rm -rf "${RELEASE_DIR}"
  exit 1
fi
```

Verified to fire correctly on 2026-04-28: a build invoked without
`SITE_CONFIG` aborts before publishing and removes the release dir.
Lab/dev work that legitimately wants the default SSID can opt in with
`ALLOW_DEFAULT_WIFI=1`.

## Publishing an OTA

After verifying the manifest:

```bash
# 1. Download artifacts to the local Mac (build host has no AWS creds).
mkdir -p ~/Downloads/1meter-fw-vX.Y.Z
scp -P 2222 -i .../uGridPLAN.pem \
  ubuntu@13.247.190.132:/opt/1meter-firmware/releases/<release>/* \
  ~/Downloads/1meter-fw-vX.Y.Z/

# 2. Sanity-check the SSID strings inside the binary.
strings ~/Downloads/1meter-fw-vX.Y.Z/FeaturedFreeRTOSIoTIntegration.bin \
  | grep -E "MAK_Wifi|DareMight|1PWR_M@k|bestcity" | sort -u

# 3. Upload to S3.
RELEASE_NAME=<release-dir-name>
S3_PREFIX="s3://1pwr-ota-firmware/firmware-releases/v<X.Y.Z>/${RELEASE_NAME}/"
aws s3 cp ~/Downloads/1meter-fw-vX.Y.Z/FeaturedFreeRTOSIoTIntegration.bin \
  "${S3_PREFIX}FeaturedFreeRTOSIoTIntegration.bin"
aws s3 cp ~/Downloads/1meter-fw-vX.Y.Z/release-manifest.json \
  "${S3_PREFIX}release-manifest.json"
S3_VER=$(aws s3api head-object --bucket 1pwr-ota-firmware \
  --key "firmware-releases/v<X.Y.Z>/${RELEASE_NAME}/FeaturedFreeRTOSIoTIntegration.bin" \
  --query 'VersionId' --output text)

# 4. Create OTA. Always start with a single-device canary on a known-good
#    customer meter (per field team: OneMeter13 = 23022673 = 0045MAK).
TS=$(date -u +%Y%m%d%H%M%S)
OTA_ID="1m-vX-Y-Z-canary-<thing>-${TS}"
# Length cap: jobId (= OTA id) must be <= 64 chars; verify with:
echo -n "$OTA_ID" | wc -c   # must be <= 64
```

Followed by `aws iot create-ota-update --ota-update-id "$OTA_ID" ...`
with target ARN `arn:aws:iot:us-east-1:758201218523:thing/<thing>`,
files JSON pointing at the S3 location and VersionId, code-signing
profile `1PWR_OTA_ESP32_v2`, and role
`arn:aws:iam::758201218523:role/1pwr-ota-service-role`.

Wait for `otaUpdateStatus` to be `CREATE_COMPLETE` (typically <30s).

## Validating the canary

```bash
aws iot list-job-executions-for-job --job-id AFR_OTA-${OTA_ID} \
  --region us-east-1 --output table
```

When the job execution shows `SUCCEEDED`, the device's next telemetry
sample should report the new `FirmwareVersion`. Cross-check via:

```bash
psql onepower_cc -c "SELECT meter_id, firmware_version, last_seen_at
                       FROM prototype_meter_state
                      WHERE meter_id = '<short-serial>'"
```

If the job stays `IN_PROGRESS` indefinitely with empty
`statusDetails`, that's the [latent cert-mismatch
signature](./1meter-ota-trust-inventory.md). Cancel:

```bash
aws iot cancel-job --job-id AFR_OTA-${OTA_ID} --force --region us-east-1
aws iot delete-ota-update --ota-update-id "${OTA_ID}" \
  --delete-stream --force-delete-aws-job --region us-east-1
```

## Rolling out to a thing-group

After the canary has soaked for a session and is reporting telemetry +
the new version:

```bash
# Same flow but targets the thing-group ARN instead of a single thing.
cat > /tmp/ota-targets.json <<'EOF'
["arn:aws:iot:us-east-1:758201218523:thinggroup/MAK_V<X_Y_Z>"]
EOF
```

For Phase 2 of the [billing migration](./1meter-billing-migration-protocol.md)
the canonical target is `MAK_V1_1_1` (= OneMeter11/13/17/18). The
trust-inventory record per-device should be updated post-OTA.

## What changed on 2026-04-28

* Patched `/opt/1meter-firmware/scripts/build_firmware_remote.sh` on
  the build host (mirrored to `scripts/ops/build_firmware_remote.sh`):
  * `release-manifest.json` now includes `router_ssid` and
    `router_password_set` so artifacts can be audited at a glance.
  * Fail-closed Wi-Fi-SSID guard: refuses to publish a release whose
    embedded SSID is `DareMightyThings` unless `ALLOW_DEFAULT_WIFI=1`
    is set.
* Built v1.1.3 with `SITE_CONFIG=site-configs/MAK.conf`. Single-device
  OTA `1m-v1-1-3-canary-OneMeter13-...` created against
  `OneMeter13` (23022673, 0045MAK) per field-team request.
* Cancelled and deleted the v1.1.2 OTA (`1m-v1-1-2-relay-cmd-...`)
  before any device completed the download. No fleet impact.
* Bumped firmware version sequence to v1.1.3 to keep the
  contaminated-1.1.2 label clearly distinguishable in any forensic
  trail (also pushed in `onepwr-aws-mesh@f16ff3d`).
* Built v1.1.4 (`onepwr-aws-mesh@50e4881`) adding two OTA operability
  improvements: per-block progress publishing to the Jobs API every
  16 blocks (so `aws iot describe-job-execution` actually shows
  percent-complete), and a coreMQTT-Agent event handler that suspends
  the periodic device reboot in `main.c::periodic_restart_task` while
  an OTA is in progress (so the existing in-RAM resume across MQTT
  reconnects isn't killed by the routine ~59 min reboot). Artifacts
  uploaded to S3 at
  `s3://1pwr-ota-firmware/firmware-releases/v1.1.4/phase2-ota-progress-mak-20260428175712-e7d8e16/`
  (VersionId `iOxFBZpgjXiNkUI1reFL4FO6ZUYW0Jbm`). **Not pushed as an
  OTA yet** — the v1.1.3 canary is still in flight; v1.1.4 will go
  out as a follow-up canary once 1.1.3 lands (or as a replacement if
  1.1.3 doesn't converge).
* Manifest fix: an earlier build-script patch had a Python
  variable-shadowing bug that left `router_ssid` always `null` in
  `release-manifest.json` (initialisers placed after the parsing loop
  instead of before). Repaired in-place on the build host and in
  the mirrored copy.

## OTA progress reporting (v1.1.4+)

Once a device runs v1.1.4 firmware and an OTA targets it, the Jobs
API surfaces real progress:

```bash
aws iot describe-job-execution \
  --job-id AFR_OTA-<ota-id> --thing-name <thing> \
  --region us-east-1 \
  --query 'execution.{status:status,details:statusDetails,updated:lastUpdatedAt}'
```

Expected `statusDetails` shape:

```json
{
  "blocks_received": "128",
  "blocks_total": "276",
  "percent": "46",
  "bytes_received": "524288"
}
```

Updates fire every 16 blocks (~64 KB). `lastUpdatedAt` ticks on each
update, giving a quick visual signal that the download isn't dead.
