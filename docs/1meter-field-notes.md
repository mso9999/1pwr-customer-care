# 1Meter field notes (MAK prototype fleet)

Operational reports from the field team. **Not** a substitute for 1PDB or AWS as source of truth for serial ↔ account mapping; use for context and follow-up.

---

## 2026-04-14 — All meters online; FW rollout; OTA loop (deferred); expansion

Source: team update (workspace could not read local path `OTA terminal.txt`; content captured from message).

1. **Connectivity:** All meters are **up** now.
2. **Firmware:** **FW updated on all** units in scope.
3. **OTA behavior (open issue, deferred):** OTA **downloads to completion** (**263 of 263** blocks), device **restarts as expected**, then **starts again from block 1** and **repeats the same loop**. Team will investigate **later** (possible stuck job, duplicate client ID, or post-update job polling behavior — see historical `SESSION_LOG` OTA / `DUPLICATE_CLIENT_ID` notes if relevant).
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
