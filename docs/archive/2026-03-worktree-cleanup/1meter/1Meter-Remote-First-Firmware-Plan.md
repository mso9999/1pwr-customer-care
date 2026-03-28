# 1Meter Remote-First Firmware Plan

## Goal

Move 1Meter operations to a model where:
- the laptop is only used for first-time provisioning / initial flash
- firmware builds, release management, OTA rollout, runtime tuning, and fleet monitoring are driven remotely from EC2 / AWS

This does **not** eliminate all field intervention. A device that is fully offline, corrupted, or physically damaged may still need physical recovery. The goal is to eliminate routine dependency on a single engineer laptop for normal operations.

## Current State

- Firmware source lives in GitHub repo `onepowerLS/onepwr-aws-mesh`.
- ESP-IDF build environment historically lived on Motlatsi's laptop; a native ESP-IDF `v5.2.3` build host now also exists on the repurposed staging EC2 under `/opt/1meter-firmware`.
- Current firmware still relies on build-time config for important settings such as:
  - WiFi SSID / password
  - Thing name
  - MQTT / TLS behavior
- We confirmed that some network settings are still hardcoded in firmware, including TLS connect / receive timeouts.
- CC / EC2 can observe fleet state well and can now build patched firmware remotely, but cannot yet remotely tune most firmware behavior at runtime.

## Target Operating Model

### Laptop Responsibilities

- Flash bootloader / partitions / base app
- Install secure cert or bootstrap credential
- Write device bootstrap manifest into NVS
- Confirm first successful join to WiFi / AWS

### EC2 / AWS Responsibilities

- Own the canonical firmware repo clone and build environment
- Build release artifacts
- Upload release bundles to S3
- Create and monitor OTA jobs
- Update runtime device config remotely
- Maintain device map, release history, and cert backup inventory
- Provide fleet health, version, and connectivity visibility

### Device Responsibilities

- Boot from a generic firmware image
- Read persistent bootstrap identity from secure storage / NVS
- Fetch desired runtime config from AWS
- Report actual runtime config / firmware version / health metrics back to cloud

## Minimum Viable Version

The minimum viable remote-first setup should allow us to do the following **without rebuilding firmware on a laptop each time**:

1. Build firmware on EC2 from `onepowerLS/onepwr-aws-mesh`
2. Push OTA artifacts to S3
3. Roll out OTA to a canary device first, then wider
4. Remotely tune a small set of runtime network parameters:
   - `tls_connect_timeout_ms`
   - `tls_recv_timeout_ms`
   - `mqtt_connack_timeout_ms`
   - `mesh_reconnect_delay_ms`
   - `data_reporting_period_s`
5. See, per device:
   - firmware version
   - config version
   - last seen
   - RSSI
   - reconnect / timeout counters

If we get only this far, we already remove most routine dependence on the laptop.

## Architecture Changes Required

### 1. Generic Firmware Image

Stop compiling per-device and per-site operational settings into the release image.

Move these out of build-time config:
- thing name
- customer / gateway role metadata
- WiFi credentials where feasible
- TLS / MQTT timeout values
- reporting interval values

Keep build-time only for:
- chip / partition / OTA layout
- feature flags that truly require code changes
- default fallback values

### 2. Runtime Remote Config

Use **AWS IoT named shadows** for persistent desired / reported device config.

Recommended shadow document fields:
- `config_version`
- `thing_name`
- `site_code`
- `wifi_ssid`
- `wifi_password`
- `tls_connect_timeout_ms`
- `tls_recv_timeout_ms`
- `mqtt_connack_timeout_ms`
- `mesh_reconnect_delay_ms`
- `data_reporting_period_s`
- `network_info_reporting_period_s`
- `ota_channel`

Recommended reported fields:
- `firmware_version`
- `config_version_applied`
- `last_boot_reason`
- `rssi`
- `wifi_disconnect_count`
- `tls_failure_count`
- `mqtt_reconnect_count`
- `uptime_s`

Use **IoT Jobs** only for imperative actions:
- OTA install
- reboot
- diagnostics collection
- cert rotation steps if needed

### 3. Remote Build / Release Environment

Create an EC2-hosted firmware build environment with:
- stable clone of `onepowerLS/onepwr-aws-mesh`
- pinned ESP-IDF version
- repeatable build script
- output folder for:
  - app binary
  - bootloader
  - partition table
  - release manifest

Preferred implementation:
- Dockerized ESP-IDF build image on EC2

Fallback implementation:
- native ESP-IDF toolchain installed on EC2

### 4. Canonical Device Registry

Maintain one canonical registry in AWS or EC2-managed storage with:
- serial number
- Thing name
- account
- role
- cert folder / cert status
- initial provisioned firmware
- current firmware version
- current config version
- OTA cohort / ring
- notes

This registry must become the source of truth for:
- field teams
- OTA targeting
- CC-side mapping checks
- cert backup audit

### 5. Provisioning Model

Short-term:
- laptop performs initial secure-cert flash and writes bootstrap config
- EC2 stores the resulting device record and backup artifacts

Long-term better state:
- move to AWS Fleet Provisioning by Claim
- laptop flashes a generic bootstrap image only
- device obtains its own AWS cert and final identity on first boot

## Workstreams

### Firmware Workstream

1. Land the timeout patch in `onepwr-aws-mesh`
2. Add runtime config struct and config versioning
3. Implement shadow sync for desired / reported config
4. Persist runtime config in NVS with sane defaults
5. Add health counters and RSSI reporting
6. Separate bootstrap identity from mutable runtime config
7. Add safe rollback / fallback if remote config is invalid

### EC2 Build / Release Workstream

1. Create stable firmware repo clone on EC2
2. Pin ESP-IDF version
3. Add build script:
   - clean
   - build
   - collect artifacts
   - write release manifest
4. Add release storage in S3
5. Add OTA job creation script
6. Add release-ring rollout script:
   - bench
   - gateway
   - single customer meter
   - small cohort
   - full site

### AWS IoT Workstream

1. Define shadow schema
2. Define config version semantics
3. Define job document schema
4. Add job templates for OTA and diagnostics
5. Add alarms / dashboards for:
   - no report in X hours
   - repeated TLS failure count
   - OTA stuck in progress

### Provisioning / Cert Workstream

1. Standardize bootstrap flash procedure
2. Backup all cert bundles centrally
3. Track cert provenance in canonical device registry
4. Decide whether to stay with local cert folders or move to Fleet Provisioning

### CC / Operations Workstream

1. Surface firmware version and config version in CC or related ops view
2. Show per-device connectivity health metrics
3. Track site-level rollout status and OTA ring
4. Track last successful remote config application

## Phased Roadmap

### Phase 0: Immediate Stabilization

Objective: remove the current single-laptop bottleneck for builds and basic OTA management.

- Move `onepwr-aws-mesh` to a stable EC2 clone
- Reproduce Motlatsi's build on EC2
- Commit the TLS timeout patch
- Build a new firmware image with higher OTA app version
- Test on one stable bench or field unit
- Store all produced binaries and manifests in S3

Exit criteria:
- EC2 can build a valid firmware image without the laptop
- we can create OTA artifacts and deploy a canary remotely

### Phase 1: Minimum Viable Remote-First Operations

Objective: make key operational tuning remote.

- Add shadow-backed runtime config
- Move timeout and reporting controls out of build-time settings
- Add reported health counters and RSSI
- Add simple EC2 scripts for:
  - push config
  - create OTA
  - inspect fleet state

Exit criteria:
- laptop no longer needed for timeout / reporting changes
- EC2 can adjust runtime config remotely

### Phase 2: Hardened Remote Operations

Objective: make fleet management safe and repeatable.

- add release-ring automation
- add central device registry
- add cert backup audit
- add alerts and dashboards
- add rollback guidance

Exit criteria:
- remote rollout is operationally safe
- device map, release map, and cert map are centrally visible

### Phase 3: Full Provisioning Modernization

Objective: reduce or remove local cert-folder dependence.

- evaluate Fleet Provisioning by Claim
- move to generic bootstrap image
- minimize per-device manual credential handling

Exit criteria:
- laptop is only needed for first physical flash / recovery
- cert lifecycle is centrally controlled

## Immediate Backlog

### Firmware

- Commit the TLS / MQTT timeout patch
- Add Kconfig-backed TLS timeout settings to the permanent repo
- Add runtime config schema in firmware
- Add shadow subscribe / apply path
- Add reported RSSI and reconnect counters

### EC2

- Create stable repo clone location
- Install or containerize ESP-IDF
- Add `build_firmware.sh`
- Add `publish_release.sh`
- Add `create_ota_job.sh`

### AWS

- Create S3 release bucket structure
- Define named shadow schema
- Define OTA job document templates
- Add CloudWatch alarms for device silence / OTA stuck state

### Operations

- Create canonical device map
- Reconcile cert backup state
- Define release ring membership
- Write one provisioning SOP and one remote rollout SOP

## Decisions Still Needed

- Do we want WiFi credentials remotely mutable, or fixed at provisioning time?
- Do we want cert rotation via jobs, or only via reprovisioning?
- Do we want EC2-native builds or Dockerized builds?
- When do we move to Fleet Provisioning?
- Where should the canonical device registry live:
  - S3 + JSON
  - DynamoDB
  - PostgreSQL

## Recommended First Three Actions

1. Put `onepwr-aws-mesh` on EC2 in a stable buildable location
2. Commit and build the timeout patch there
3. Implement the first shadow-driven runtime config keys for timeout tuning

That sequence gives the fastest path to real remote operational control.
