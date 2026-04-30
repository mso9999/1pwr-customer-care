# 1Meter v1.1.10 field-flash plan

> **TL;DR.** v1.1.7 fixed MQTT (us-east-1 endpoint pin). v1.1.9 added
> always-on diag heartbeat + reconnect Job re-poll. v1.1.10 fixes OTA
> throughput on poor-RF links by raising the MQTT-Agent data-buffer pool
> from 2 to 16 and the blocks-per-request from 4 to 8. Empirically v1.1.9
> got ~0.9 blocks/min on OneMeter11 at -80 dBm; v1.1.10 should land a
> 1.1 MB image in ~20–30 min on the same link.
>
> Like v1.1.6/v1.1.7 before it, **v1.1.10 ships via serial flash for the
> first hop** — devices currently on v1.1.7/v1.1.9 don't have the
> throughput fix yet, so OTA-pushing v1.1.10 over the existing config
> takes the same multi-hour download. Once the fleet is on v1.1.10 every
> subsequent OTA can be cloud-pushed in minutes.

## What changed in v1.1.10

Three layers of fix on top of v1.1.9, motivated by the OneMeter11 hang
at block 272/277 (98%) on 2026-04-30 — that device went silent for 6+ h
with no recovery vector because the OTA agent left
`s_otaInProgress=true` and blocked main.c's periodic_restart safety net.

### A. Throughput

Two-knob fix in `ota_over_mqtt_demo.c` + `sdkconfig.defaults`:

1. **`NUM_OF_BLOCKS_REQUESTED` 4 → 8** — halves the number of MQTT
   round-trips per N blocks downloaded.
2. **`CONFIG_GRI_OTA_MAX_NUM_DATA_BUFFERS` 2 → 16** — the upstream
   Kconfig default of 2 was the real bottleneck. With 4 blocks per
   GetStream and only 2 in-flight buffers, the second half of every
   burst was being dropped silently in the incoming-publish callback
   and re-requested. RCA was visible in cloud logs as ~4× more
   `Publish-Out` events on the OTA stream than the device acked.

   Memory cost ≈ 16 × 4.5 KB = 72 KB on the 400 KB SRAM ESP32-C3 —
   safely below the existing mesh / Wi-Fi / MQTT / httpd / meter-task
   budget.

### B. Hang recovery

3. **`tasks/ota_watchdog/`** — new low-priority FreeRTOS task. Polls
   `vOtaDiagIsActivelyDownloading()` and `vOtaDiagGetMsSinceLastBlock()`
   every 30 s. If the agent claims to be in
   `CreatingFile/RequestingFileBlock/WaitingForFileBlock/ClosingFile`
   but `currentBlockOffset` hasn't advanced for 5 min and uptime > 2 min,
   logs the diagnostic state and calls `esp_restart()`. Recovers from
   the class of hangs that leave `s_otaInProgress` stuck and block the
   main.c periodic_restart safety net. Min uptime gate avoids
   false-positive bites at boot during the brief CreatingFile phase.

4. **NVS-persisted OTA progress** in `ota_over_mqtt_demo.c`. Every
   `OTA_PROGRESS_REPORT_INTERVAL_BLOCKS` (= 16) we save
   `(jobId, currentBlockOffset, blocksTotal)` to NVS namespace
   `ota_resume`. On a fresh boot, when the agent receives a job
   document with the **same** jobId, we fast-forward the in-memory
   counters to the saved offset before issuing the first
   `RequestFileBlock`, so the watchdog bite (or any reboot mid-OTA)
   resumes from the last 16-block checkpoint instead of starting from
   0. NVS is cleared on OTA success and on close-file / activate-image
   failure. ~10–20 NVS writes per 1.1 MB OTA, well within
   wear-levelling headroom.

### C. End-of-OTA observability

5. **End-of-OTA log markers** in `ota_over_mqtt_demo.c`. Every
   `OtaAgentEventCloseFile` and `OtaAgentEventActivateImage` step now
   emits an `ESP_LOGW`/`ESP_LOGE` tagged `[ota-finalize]` with the
   block count + outcome, so the next time the tail of an OTA hangs
   we can see in IoT logs / serial console exactly which step failed
   (signature verify? partition switch? activation?).

6. **Diag heartbeat fields** in `ota_diag.c`. Every 15 s
   `diag/<thing>/heartbeat` now also carries:
   - `ota_active` — 1 if the agent is in an active-download state
     (else 0). Cloud-side gate for "is the watchdog supposed to be
     watching?".
   - `ota_last_block_age_s` — seconds since `currentBlockOffset` last
     advanced; `-1` if no block has been received this boot. Lets ops
     spot a stalled tail-of-download well before the on-device watchdog
     bites.

Also carried forward from v1.1.9:

- Always-on `ota_diag` heartbeat on `diag/<thing>/heartbeat` every 15 s
  with OTA agent state, block offset, blocks remaining, RSSI, mesh
  layer, free heap. Routed by `onemeter_diag_rule` into CloudWatch
  `/iot/onemeter-diag`.
- `OtaAgentEventRequestJobDocument` re-fired on every MQTT
  CONNECTED event, so a queued OTA picked up within seconds of the next
  reconnect rather than waiting for the AWS-pushed `notify-next` (which
  has been unreliable on this fleet).

And from v1.1.7:

- `CONFIG_GRI_MQTT_ENDPOINT` pinned to `a3p95svnbmzyit-ats.iot.us-east-1.amazonaws.com`
  (without this Kconfig falls back to the Amazon FreeRTOS SDK example
  endpoint in us-west-2, which silently rejects our device certs).

## Evidence v1.1.10 is the right next step

OneMeter11 v1.1.9 OTA download (in progress, 18:00–18:30 UTC):

| time (UTC) | blocks | % | bytes |
|---|---|---|---|
| 17:30 | 0 | 0% | 0 |
| 17:36 | 16 | 5% | 65 536 |
| 17:56 | 32 | 11% | 131 072 |
| 18:16 | 48 | 17% | 196 608 |

Cloud-side stream telemetry over the same window: **132 outbound block
publishes from AWS, only ~32 cloud-acked received** — 4× drop ratio,
matching the buffer-pool-of-2 pigeonhole. v1.1.10 closes the gap.

ETA on v1.1.10's own OTA download (post field flash) for a 1.1 MB
image at -80 dBm: ~20–30 min, vs ~5 h on v1.1.9.

## Devices to flash

The 12 deployed Things at MAK as of 2026-04-29:

| Thing | Meter | Notes |
|---|---|---|
| OneMeter5 | … | |
| OneMeter11 | 000023022613 | **Currently hung — needs powercycle FIRST.** v1.1.9 OTA reached 272/277 (98%) on 2026-04-30 00:58 UTC, then device went silent (zero IoT events for 6+ h). OTA agent likely crashed in the tail-end of the download with `s_otaInProgress=true`, blocking the periodic_restart safety net. v1.1.9 OTA has been cancelled cloud-side. After powercycle the device will boot back to v1.1.7 cleanly. Then serial-flash v1.1.10 over USB. |
| OneMeter12 | … | |
| OneMeter13 | … | |
| OneMeter14 | … | |
| OneMeter15 | … | |
| OneMeter16 | … | |
| OneMeter17 | … | offline since 28-Apr 09:33 — investigate while there |
| OneMeter18 | … | |
| OneMeter19 | … | |
| OneMeter44 | … | |
| OneMeter122 | … | |

3 newly added devices not yet built:
- 23022684 / 0051MAK / OneMeter19 (now in fleet build)
- 23021886 / 0056MAK / OneMeter122 (offline ~5 days — investigate)
- 23021888 / 0058MAK / OneMeter12

## Flash procedure

Same as v1.1.6/v1.1.7. The bundle ships with `Flash-OneMeter.ps1`
(Windows) and step-by-step `esptool` commands for Linux/macOS. From
the per-Thing folder:

```powershell
.\Flash-OneMeter.ps1 -ThingName OneMeter13
```

(auto-detects COM port, sanity-checks the embedded Thing name matches
the target device, runs `esptool` with the correct flash offsets).

## Validation after flash

1. `idf.py monitor` for ~2 min. Look for:
   - `OTA over MQTT demo, Application version 1.1.10`
   - `TLS connection established`
   - `MQTT connection established with the broker`
   - `Subscribed to oneMeter/<thing>/cmd/relay`
   - `OTA_WD: OTA watchdog started (poll=30s, hang=300s)` (v1.1.10 mark)
   - `OTA_DIAG: diag: {...}` lines every ~15 s — JSON should now
     include `"ota_active"` and `"ota_last_block_age_s"`
2. Cloud-side, within ~1 min:
   - DDB `meter_last_seen` should refresh.
   - `aws logs tail /iot/onemeter-diag --follow` should show
     `{"thing":"<thing>","fw":"1.1.10","ota_active":0,...}` heartbeats.
3. After all 12 devices are on v1.1.10:
   - Push a no-op v1.1.11 canary to ONE Thing. Should converge in
     ~20–30 min vs v1.1.9's ~5 h on the same link.
   - During the canary download, watch
     `aws logs tail /iot/onemeter-diag --follow --filter-pattern '"<thing>"'`
     — `ota_active` should be `1` and `ota_last_block_age_s` should
     stay below ~120 s. If it climbs past 300 s the watchdog will
     bite, the device will reboot, and the agent will resume from the
     last 16-block checkpoint.

## Failure-mode reference

| Symptom | What it means | Action |
|---|---|---|
| `ota_active=1` and `ota_last_block_age_s` climbing past 300 | watchdog about to bite | wait — recovery is automatic |
| Device reboots cloud-side, comes back, OTA resumes mid-image | NVS resume working as intended | none |
| Device silent > 30 min mid-OTA, no reboot logged | hard freeze (watchdog task not running) | physical powercycle; investigate |
| `[ota-finalize] CloseFile FAILED` in logs | signature verify or partition write failed | inspect serial log; usually means a corrupted / mis-signed image |
| `[ota-finalize] ActivateImage FAILED` | partition switch failed | rare; may indicate flash wear; reflash via serial |

## Open issue: end-of-OTA hang on v1.1.7→v1.1.9 download

**Observed 2026-04-30 00:58 UTC on OneMeter11.** The OTA agent reached
272/277 blocks (98%, full 1.1 MB downloaded) then stopped emitting any
MQTT traffic — no further block requests, no telemetry, no Jobs status
updates. Device was hard-hung; required physical powercycle to recover.

The hang sits between block N-5 reception and the
`OtaJobEventActivate / OtaJobEventStartTest` transition. Possible causes
to investigate next time we have serial console access:

- **Buffer-pool exhaustion on the tail.** With `MAX_NUM_OTA_DATA_BUFFERS=2`
  the agent had been operating in starvation mode the whole download;
  could be a related corner case at end-of-stream.
- **Signature verification failure** that doesn't bubble back as a Jobs
  status update (mbedTLS RSA-3072 verify can be heavy; possible task
  starvation or watchdog).
- **`s_otaInProgress` flag stuck true** because the agent never sent
  `CORE_MQTT_AGENT_OTA_STOPPED_EVENT`, so `periodic_restart_task`'s
  1-hour safety-net never fired.

**Mitigations baked into v1.1.10:**
- Buffer pool 2→16 — likely closes the starvation path.
- Reconnect `RequestJobDocument` re-poll — gives a recovery vector if
  agent comes back from a reconnect post-hang (won't help a true
  freeze, but covers softer stalls).

**Mitigations to consider for v1.1.11+ if the hang recurs:**
- Wire a hard watchdog on the OTA agent task (`esp_task_wdt_add()`)
  with a 5-min timeout. On bite, reboot + `currentBlockOffset` is lost
  but at least we recover.
- Persist `currentBlockOffset` + jobId to NVS every N blocks so a
  reboot can resume mid-download.
- Decouple `s_otaInProgress` from the OTA agent's own state — set it
  on `RequestFileBlock` events, clear it on a 60-s no-progress timer.
