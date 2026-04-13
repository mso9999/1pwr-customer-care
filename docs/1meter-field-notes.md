# 1Meter field notes (MAK prototype fleet)

Operational reports from the field team. **Not** a substitute for 1PDB or AWS as source of truth for serial ↔ account mapping; use for context and follow-up.

---

## 2026-04-13 — Wi‑Fi, PCB swaps, firmware

**Session context:** Site Wi‑Fi was addressed by IT; MAK 1Meter fleet was moved to the new network.

1. **Wi‑Fi (IT):** The reported Wi‑Fi issue was **resolved by IT**.
2. **PCB replacements:** Two PCB swap operations were performed: **44 → 17** and **55 → 11** (reported as written by the team).
3. **Performance:** Wi‑Fi is **reported to be faster** than before the change.
4. **Connectivity:** **All meters joined the new Wi‑Fi** with the same credentials as the previous network **except serial `23022628`** (still not on the new SSID at time of report).
5. **Firmware:** Only **two** PCBs are on the **latest firmware** at time of report: **PCB 17** and **PCB 11** (devices **`onemeter17`** and **`onemeter11`** respectively in the naming scheme used on devices / AWS Things).

### Follow-up

- **`23022628`:** Investigate why this unit did not associate (credential entry, signal, hardware, or stale provisioning vs other units).
- **Fleet FW:** Plan rollout or OTA so remaining units match the two already on latest FW, if required by the 1Meter program.
