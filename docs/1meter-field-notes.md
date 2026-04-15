# 1Meter field notes (MAK prototype fleet)

Operational reports from the field team. **Not** a substitute for 1PDB or AWS as source of truth for serial ↔ account mapping; use for context and follow-up.

---

## 2026-04-15 — OneMeter13 / 23022673: OTA log (RCA notes)

Source: serial log excerpt (team). **Do not commit live Wi‑Fi SSIDs/passwords** into this repo; this section summarizes behavior only.

**Device:** AWS Thing **`OneMeter13`**, meter SN **`23022673`** (see `CONTEXT.md` → 0045MAK check meter).

**What the log shows (chronological):**

1. **OTA download completes:** Blocks **261→262 of 263**, “Close file”, **`Signature verification succeeded`**, **`Activate Image event Received`**, `esp_image` maps segments (firmware being validated / staged for boot).
2. **Before the new image can settle:** **`wifi:state: run -> init`**, **`Disconnect reason : 8`** (STA disconnect), **`coreMQTT` receive fails**, OTA agent **suspended** on MQTT disconnect, then **`rst:0xc (RTC_SW_CPU_RST)`** — **software-initiated reset** during/just after activation, while MQTT was already lost.
3. **Interpretation (likely “loop” cause):** The **OTA image transfer and signature step succeed**, but the **IoT job may never reach `SUCCEEDED` in AWS** if the device **resets or drops MQTT** before the agent acknowledges completion. On next boot the **same job** can still be **active**, so the device **requests the stream from block 1 again** → looks like an infinite download loop even though each pass completes 263/263 locally.
4. **After reboot:** App reports **`OTA over MQTT ... Application version 1.0.7`**; **`Checksum mismatch between flashed and built applications`** warning (worth confirming build/flash alignment on this unit); **`phy_init` RF calibration** fell back to full calibration (one-time after flash/reset); **Mesh-Lite** repeatedly **`ap record is NULL`** — **no suitable uplink AP seen** for an extended period (RF/environment/config — separate from OTA crypto).

**Suggested follow-up (firmware/AWS ops, not CC code):**

- In **AWS IoT Core**: check job **`AFR_OTA-...`** / stream for **`OneMeter13`** — if status stays **`IN_PROGRESS`** or **`QUEUED`** while the device reboots, cancel or complete the job after confirming the running **1.0.7** image, or fix **MQTT stability through the activation window**.
- On **device**: reduce **Wi‑Fi / mesh disconnects during OTA activation**; confirm **no unintended `esp_restart`** path overlapping activation; resolve **checksum** warning if builds are meant to match.

---

## 2026-04-14 — All meters online; FW rollout; OTA loop (deferred); expansion

Source: team update (workspace could not read local path `OTA terminal.txt`; content captured from message).

1. **Connectivity:** All meters are **up** now.
2. **Firmware:** **FW updated on all** units in scope.
3. **OTA behavior (open issue, deferred):** OTA **downloads to completion** (**263 of 263** blocks), device **restarts as expected**, then **starts again from block 1** and **repeats the same loop**. Team will investigate **later**. **2026-04-15 addendum:** see section above — log on **`OneMeter13` / 23022673** suggests **MQTT drop + reset during activation** may prevent cloud job completion, so the **same job** re-downloads from block 1; also check historical `SESSION_LOG` **`DUPLICATE_CLIENT_ID`** if multiple certs fight the same client ID.
4. **Rollout plan:** Expect to **add more meters between tomorrow and Friday** (calendar window as stated by team).

---

## 2026-04-13 — Wi‑Fi, PCB swaps, firmware

**Session context:** Site Wi‑Fi was addressed by IT; MAK 1Meter fleet was moved to the new network.

1. **Wi‑Fi (IT):** The reported Wi‑Fi issue was **resolved by IT**.
2. **PCB replacements:** Two PCB swap operations were performed: **44 → 17** and **55 → 11** (reported as written by the team).
3. **Performance:** Wi‑Fi is **reported to be faster** than before the change.
4. **Connectivity:** **All meters joined the new Wi‑Fi** with the same credentials as the previous network **except serial `23022628`** (still not on the new SSID at time of report).
5. **Firmware:** Only **two** PCBs were on the **latest firmware** at that report: **PCB 17** and **PCB 11** (devices **`onemeter17`** and **`onemeter11`** respectively in the naming scheme used on devices / AWS Things).

### Follow-up (superseded in part by 2026-04-14)

- Subsequent team update (2026-04-14) reports **all meters up** and **FW on all** — treat **`23022628`** and fleet FW as **resolved** unless contradicted by telemetry.
