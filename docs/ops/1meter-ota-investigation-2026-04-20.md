# 1Meter OTA canary — investigation log (2026-04-18 → 2026-04-21)

## Result

**Two OTA canary attempts on `OneMeter13` (`23022673` / `0045MAK`) failed silently.** Device is running the old firmware; MQTT keeps reporting normally; no `FirmwareVersion` field in telemetry; `IoT Jobs` execution sat at `IN_PROGRESS` with empty `statusDetails` for hours and the device never reported progress back.

## Attempts

| OTA id | Signing profile | ACM cert SHA1 | Outcome |
|---------|-----------------|---------------|---------|
| `1meter-v1-1-0-canary-OneMeter13-20260418094036` | `1PWR_OTA_ESP32_v2` | `03:9E:44:43:40:A2:85:A5:...` | Stuck 11h; cancelled |
| `1meter-canary-OM13-mak-20260420175921` | `MAK_OTA_Profile` | `CB:98:92:F4:82:38:C6:26:...` | Same pattern; cancelled |
| (not tried — same cert as above) | `ESP32C3PROFILE` | `CB:98:92:F4:82:38:C6:26:...` | Skipped (identical fingerprint) |

Repo-embedded cert at `onepwr-aws-mesh/main/certs/aws_codesign.crt` fingerprint: `18:92:8E:D3:2F:EF:51:12:...` — **matches neither** of the two available signing profile certs. The corresponding private key is not in any AWS signing profile we have access to.

## Evidence of silent rejection

- During each download window, telemetry stopped (consistent with firmware commit **`5a32b35` — "OTA: suspend meter reads during OTA"**), then telemetry resumed ~11 h later running the same old image.
- Energy counter advanced normally (16.81 → 16.95 kWh), so the device rebooted OK and is healthy.
- Executing OTA twice with **two different signing keys** produced the identical stuck pattern — strong signal the device is failing signature verification, not a cert-specific problem.
- AFR-OTA **should** publish progress to `$aws/things/OneMeter13/jobs/$next/update` (`DevicePolicy` already grants that). The absence of any `statusDetails` update suggests the device's signature-verify step aborts before the status-update codepath runs.

## Why only `OneMeter6` ever succeeded (March)

The one historical success (`AFR_OTA-live-v1_0_2-MAKGroup`, 1 SUCCEEDED / 7 CANCELED) used `1PWR_OTA_ESP32_v2`. `OneMeter6` was presumably flashed with firmware containing the matching `03:9E:44:...` cert. The 7 that CANCELED likely had a different embedded cert — same failure mode we're seeing today. The repo cert has since been updated to `18:92:8E:...`; no new signing profile was created with the matching private key, so this issue has been latent ever since.

## Forward paths

| Option | What it takes | Pro | Con |
|--------|---------------|-----|------|
| **Serial flash v1.1.0 on one device** | Field visit (planned for next MAK mission) | Produces a known-good seed, proves the new image boots and publishes `FirmwareVersion`, future OTAs from that device onward will succeed (consistent cert). | Requires physical access. |
| **Regenerate matching key pair**, publish new ACM cert, update firmware `aws_codesign.crt` **and** create matching signing profile, rebuild image. | Desk work + one final reflash. | Gives us a fleet-capable OTA story again. | Need to safely manage the private key. |
| **Custom code-signing** at OTA creation time (embed the cert-chain that matches the device). | Requires possession of the device's embedded cert **and** its private key (outside AWS). | No new flash needed. | Key likely lost; otherwise same as option 2. |

Recommendation: **take serial flash during the next field visit**. At that time, also capture the device's embedded cert from the running firmware (or reflash with the current repo cert + a signing-profile matched key pair, documented) so the next deployment has a consistent OTA trust chain.

## Cleanup (completed 2026-04-21)

- Cancelled + deleted both stuck OTA updates.
- No IoT jobs left targeting `OneMeter13`; device free to receive future updates.
- Left `sdkconfig.defaults` at `1.1.0` on the build host so any re-signed rebuild stays anti-rollback safe.

## References

- Session logs: 2026-04-18 `202604181000` (canary created) and 2026-04-21 (this doc).
- Earlier cert-mismatch fix (2026-02-21): `SESSION_LOG.md` — "Fix OTA signature verification: embed correct ACM code signing certificate".
- Build host runbook: `docs/archive/2026-03-worktree-cleanup/1meter/1Meter-Remote-Build-OTA-Runbook.md`.
- Firmware repo: `onepwr-aws-mesh` (commit `21b8586` adds `FirmwareVersion` to MQTT; `5a32b35` suspends meter reads during OTA).
