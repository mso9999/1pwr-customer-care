# Nexus all-sites list ↔ uGridPlan canonical designs — review list (25 Sep 2026)

Rule: every canonical uGP design has a site in the Nexus all-sites list (PR `referenceData_sites`); PR-only sites may exist without a design. Backfill policy (your decision): auto-bind only exact matches — PR site `XXX` in an operating org and uGP design `XXX_minigrid` in the same country. Everything else is listed here for you to decide.

## A. Bound automatically (26)

PR `canonicalUgpProjectId` set; uGP marks these designs canonical on its next 30-min sync and then pushes coordinates (gensite, else element centroid). Pre-bind state is in Firestore `siteSyncRepairs` (`kind: backfill-exact-bind`).

- **Benin (14):** AFN, AGL, DAM, DME, DOD, GBO, IGB, KIN, KOT, SAL, SAM, SIN, TAB, TAN — plus DON, already canonical (`DON_minigrid`).
- **Lesotho (12):** KET, LEB, LSB, MAK, MAS, MAT, RIB, SEB, SEH, SHG, TLH, TOS.

Check: **SIN** was bound to `SIN_minigrid` ("Sinlita"). uGP also has SI1 (Sinlita BT), SI2 (BT new), SI3 (MT-BT new), SI4 (MT-BT Triphasé). If SI3/SI4 is the build, use **Make canonical** on it in uGP (records the handoff).

## B. PR sites that need a decision

| Country | PR site | Likely match in uGP | Decision needed |
|---|---|---|---|
| BJ | **AKU** AKEKEROU | `AKE_minigrid` Akékérou | Same place, different code. Keep AKU (rename the uGP design's code) or change PR to AKE — then bind. |
| BJ | **AGK** AHOME GBEKPA | `AGB_minigrid` AHOMEY_GBEKPA (also AHC/AHN/AHH variants) | Same as above: pick one code, then bind. |
| BJ | **DIN** DINGOU | `DNG_minigrid` DINGOU (and sandbox DIG) | Same as above. |
| BJ | BOH Bohicon Bureau | — | Office, not a minigrid: leave as a PR-only site. |
| LS | **NKU** Ha Nkau | `NKU_ci` only | Is the C&I design the canonical design? If yes, mark it canonical in uGP. CC still maps NKU → uGP key `NKA`. |
| LS | **BOB** Bobete, **MAN** Manamaneng, **MET** Methalaneng | `BOB_ci`, `MAN_ci`, `MET_ci` only | Same question. |
| LS | RAL Ha Raliemere | — | New PR site with no design yet — fine until a design exists. |
| LS/BJ | HQ | — | Two-letter code; offices. Leave as PR-only. |

## C. uGP top-level designs with no Nexus site

These stay unbound until someone marks them canonical (which now registers the site, with the code check and 300 m check).

- **Benin, probably real sites:** AAH/ADJ/AHD (Ahodjinako ×3), AHO Ahokanmey, AKO Akouessa, AVE Avlame, AVH Avloh, AYA Ahogbéya, GBA Gbakpodji, GOB Gobaix, GOD Godohou, KOU Koussoukpa, KPO Kpokissa, SEO/SET Setto (×2), TCA Tchi-Ahomadégbé, TCH Tchito. Duplicates to resolve: IGE vs IGB (IGBERE), DNG vs DIN, AGB vs AGK, AKE vs AKU.
- **Zambia:** CHA Chapita, CPG Chipungu, MAZ/MTZ Matambazi (×2), MPH Mphita, MTU Mtukuzi, SEY Seya. PR has no `1pwr_zambia` sites yet.
- **Not sites — should be marked sandbox:** 38 Lesotho "Tutorial sandbox" designs (AHG, BGJ, BQB, …, ZGG), BJ TRN/TNN (Training), and GBW (no country). They carry no role today, so they appear in lists as unclassified.
