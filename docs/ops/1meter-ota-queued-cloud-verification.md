# 1Meter OTA stuck at QUEUED — cloud-side verification runbook

Context: a newly provisioned 1Meter gateway (e.g. the KOT canary, provisioned
2026-09-01) shows its OTA job as `QUEUED` and never progressing. This runbook
verifies, from the cloud side, whether the gateway has ever reached AWS IoT,
and narrows down where the chain is broken. All steps are read-only.

## What QUEUED means

- The OTA status view reads AWS IoT Job executions via
  `GET /api/provisioning/ota/{ota_update_id}`
  (`acdb-api/meter_provisioning.py`).
- `QUEUED` = the AWS job execution exists but the gateway has never started it
  (`started_at` is null).
- OTA updates are created with `protocols=["MQTT"]` and
  `targetSelection="SNAPSHOT"`. A snapshot job execution stays QUEUED
  indefinitely until the Thing connects to AWS IoT and requests its job. It
  does not expire on its own.
- Therefore "stuck at QUEUED" and "gateway is not online" are the same fact:
  the gateway has not connected to AWS IoT since the job was created. The job
  is fine and starts by itself once the gateway connects. Do not recreate the
  job or re-provision the unit.

## Step 0 — Collect the identifiers (CC portal, no AWS needed)

Record four values before touching AWS: **Thing name**, **PCB MAC**, **OTA
update ID**, **AWS IoT Job ID**.

1. In CC, open **Provisioning → Provisioned meters** (backed by
   `GET /api/provisioning/meters?site=KOT`). Find the unit's row and record:
   - `thing_name` — `KOT-GW-####`;
   - `pcb_mac` — used to spot the gateway in the router's client list;
   - `ota_update_id`, `ota_status`, `ota_target_version`, `fw_version`;
   - `last_seen_online` — if null, CC has never seen telemetry from it;
   - `deployment_wifi_ssid` — the SSID CC recorded at bootstrap. Compare this
     against what the mirror router is actually broadcasting.
2. Cross-check against the provisioning station's exported CSV (same Thing
   name and OTA update ID).
3. Optionally `GET /api/provisioning/registry` for the DynamoDB registry row
   (PCB MAC ↔ Thing ↔ certificate ID binding).

## Step 1 — Read the OTA job execution state (CC, operator level)

Use the station's **Check OTA status** button, or call directly:

```
GET https://cc.1pwrafrica.com/api/provisioning/ota/{ota_update_id}
```

In the response:

- `ota_status: CREATE_COMPLETE` only means AWS created the update — not that
  anything was installed.
- `aws_iot_job_id` — record this; it is the underlying Job ID.
- `executions[]` — one entry per Thing. `status: "QUEUED"` with
  `started_at: null` is the formal proof that the device has never picked up
  the job.

## Step 2 — Check Thing connectivity (decisive check)

Use the gateway-health endpoint, which queries the fleet index with a per-site
pattern (`thingName:{site}-GW*`), so it covers KOT things:

```
GET https://cc.1pwrafrica.com/api/sync/gateway-health?site=KOT&gateway_thing=KOT-GW-####
```

Interpretation:

| `state`  | Meaning |
|----------|---------|
| `online` | MQTT session up right now (or contact <24h ago) |
| `recent` | last contact 24–72h ago |
| `offline`| connected once, but >72h ago |
| `never`  | no connectivity record at all — the gateway has never reached AWS IoT |

Do **not** use CC's **Fleet live** page for this check: it only queries
`thingName:OneMeter*` and `thingName:MAK-GW*`, so a `KOT-GW-*` Thing is
invisible there even when it is connected.

## Step 3 — AWS-side verification (Engineering)

Requires AWS read access to account `758201218523`, region `us-east-1` unless
the CC host sets `AWS_DEFAULT_REGION` differently. The CC host's own IAM role
already has the OTA/fleet-index read permissions, so these commands can also
be run from the CC host.

### a) Fleet-index connectivity (raw form of Step 2)

```bash
aws iot search-index --query-string "thingName:KOT-GW*" --region us-east-1
```

Check each thing's `connectivity` block: `connected`, `timestamp` (last
connect/disconnect event), `disconnectReason`. No `connectivity` block at all
= never connected.

### b) Thing, certificate, and policy health

```bash
aws iot describe-thing --thing-name KOT-GW-####
aws iot list-thing-principals --thing-name KOT-GW-####
aws iot describe-certificate --certificate-id <cert-id-from-registry>
aws iot list-attached-policies --target <certificate-arn>
```

Verify the certificate is `ACTIVE`, attached to the Thing, and the attached
policy allows `iot:Connect`, `iot:Subscribe`, `iot:Receive`, `iot:Publish`
(including the Jobs topics). CC created all of this at allocation, so it
should be correct — but a detached or inactive certificate produces exactly
this symptom.

### c) OTA update and Job state

```bash
aws iot get-ota-update --ota-update-id <ota_update_id>
aws iot list-job-executions-for-job --job-id <aws_iot_job_id>
aws iot describe-job-execution --job-id <aws_iot_job_id> --thing-name KOT-GW-####
```

Confirm the job's `targets` contain
`arn:aws:iot:<region>:758201218523:thing/KOT-GW-####` and the execution is
`QUEUED` with no `startedAt`.

### d) Protocol-level evidence (if needed)

Check AWS IoT Core logs in CloudWatch (if IoT logging is enabled) for
`CONNECT` events/failures for client ID `KOT-GW-####`:

- nothing in the logs → the device never tried to connect → problem is on the
  device/network side;
- connect attempts rejected → certificate/policy side.

Confirm the endpoint the device must reach:

```bash
aws iot describe-endpoint --endpoint-type iot:Data-ATS
```

## Step 4 — Decision tree

- **`gateway-health` = `never` + job QUEUED + nothing in IoT logs** → the
  gateway has never reached AWS IoT. The break is between the gateway and the
  internet: mirror SSID/password not matching what was entered at bootstrap,
  5 GHz-only AP, no internet backhaul, blocked outbound TCP 8883, or a captive
  portal. Work through the mirror checklist below. The router's DHCP/client
  list (does the PCB MAC appear?) tells you whether the gateway even joined
  the Wi-Fi.
- **`connected: true` but job still QUEUED** → the device connects but is not
  picking up the job. Check the certificate policy covers the Jobs topics and
  the job target ARN matches the Thing; then have Engineering power-cycle the
  gateway or re-create the OTA update against the same Thing.
- **`connected: false` with an old timestamp and a `disconnectReason`** → it
  connected at some point and dropped. Check power and mirror stability; the
  job resumes by itself when it reconnects.

## Appendix — mirror network checklist (device side)

The gateway gets no network through the laptop. After bootstrap it leaves the
`1Meter` provisioning LAN and must join the operational network (the mirror
router) by itself, using the credentials entered in the station form, then
reach AWS IoT over that network's internet (outbound TCP 8883, MQTT only,
plus DNS/NTP for TLS).

1. Mirror SSID/password exactly match what was typed in the station form at
   provisioning (intended: the exact site Starlink credentials) —
   capitalization, spaces, punctuation included.
2. 2.4 GHz band, WPA2 (the ESP32 does not see 5 GHz-only networks).
3. Internet backhaul on the mirror, verified with a phone using the mirrored
   credentials, then disconnect the phone.
4. Outbound TCP 8883 allowed, plus DNS/NTP; no captive portal; client/guest
   isolation off.
5. Mirror broadcasting and within range continuously since bootstrap.
6. Definitive local test: the mirror router's DHCP/client list should contain
   the gateway's PCB MAC. If the gateway is still visible on the `1Meter`
   provisioning LAN instead, it never moved.

## Cautions / stop conditions

- Do not create a second Thing or a second OTA update while diagnosing; all
  steps above are read-only.
- Do not erase or reflash the unit.
- If the wrong Wi-Fi credentials were entered at bootstrap, recovery is a
  controlled re-delivery of corrected Wi-Fi configuration against the **same**
  registry entry — an Engineering operation. Escalate with Thing name, PCB
  MAC, and OTA update ID.
- Fleet-index `connectivity.timestamp` is the last connect/disconnect *event*
  time, not a heartbeat — read it together with `connected`.
- Per the provisioning SOP, a job remaining queued beyond the agreed rollout
  window is a stop-and-escalate condition.

Related: `docs/ops/1meter-factory-ota-release-gate.md`,
`acdb-api/provisioning_station_dist/START_HERE_FACTORY_VIRGIN_GATEWAYS.md`.
