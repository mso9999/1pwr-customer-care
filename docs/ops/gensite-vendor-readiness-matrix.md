# Gensite vendor readiness matrix

This is the operational reference for integrating non-Deye generation OEMs into
CC through the shared gensite pipeline.

## Current readiness

| Vendor | Adapter | Credential backend | Poller support | Live telemetry | Alarm ingestion | Integration status |
|---|---|---|---|---|---|---|
| Victron | `victron.py` | `vrm` | yes | yes | yes | ready |
| Sinosoar | `sinosoar.py` | `sinosoarcloud` | yes | yes | no (currently returns none) | ready (live only) |
| SMA | `sma.py` | `sunny_portal` | yes | yes (`energybalance` live + day) | no (currently returns none) | ready (live + day) |

## Production snapshot (2026-05-25)

- Sinosoar credentials verified on CC host: **9 / 9 success** after retry pass.
- Victron configured credentials currently detected on host: **1** (`GBO`).
- SMA configured credentials currently detected on host: **4** (`BOB`, `MAN`, `MET`, `NKU`).
- SMA credential test account currently sees **7 plants** in `/api/v1/navigation` (`6060012`, `6202555`, `6360299`, `6581580`, `6716623`, `6727482`, `8463703`).
- SMA host verification now **4 / 4 success** with mapped `site_id_on_vendor`:
  - `BOB -> 6202555`
  - `MAN -> 6360299`
  - `MET -> 6727482`
  - `NKU -> 6060012`
- One-shot poller execution confirms live SMA rows written to `inverter_readings` for all 4 mapped LS sites.
- `fetch_day` smoke test on host (2026-05-25 local day) returns populated interval sets:
  - `BOB=179`, `MAN=179`, `MET=183`, `NKU=179` rows.
- Victron host verification now **1 / 1 success** with mapped `site_id_on_vendor`:
  - `GBO -> 372788` (`GBOWELE`, identifier `c0619ab4463f`)
- One-shot poller execution confirms Victron rows for `GBO`; latest normalized
  mapping includes non-null `pv_kw`, `ac_kw`, `battery_kw`, `battery_soc_pct`,
  `grid_kw`, `ac_freq_hz`, `ac_v_avg`.

Reports:
- `docs/ops/gensite_verify_2026-05-25_1343_sinosoar_only.csv`
- `docs/ops/gensite_verify_2026-05-25_1357_victron_sma.json`
- `docs/ops/gensite_verify_2026-05-25_1404_sma.csv`
- `docs/ops/gensite_verify_2026-05-25_1413_sma.csv`
- `docs/ops/gensite_verify_2026-05-25_1413_sma_mapped.csv`
- `docs/ops/gensite_verify_2026-05-25_1525_victron.csv`
- `docs/ops/gensite_verify_2026-05-25_1526_victron_mapped.csv`

## Required credential fields

| Vendor | Required now | Optional | Notes |
|---|---|---|---|
| Victron | `username`+`secret` OR `api_key` | `site_id_on_vendor`, `base_url` | Prefer API token (`api_key`) where possible |
| Sinosoar | `username`, `secret` | `site_id_on_vendor`, `base_url` | CAPTCHA OCR dependency on poller host (`tesseract` + `pytesseract`) |
| SMA | `username`, `secret` | `site_id_on_vendor`, `base_url`, `extra.client_id` | Uses Keycloak token (`SPpbeOS` default client) + `uiapi.sunnyportal.com` |

## Bulk verification command

Use this to validate as many configured credentials as possible in one pass.

```bash
python3 scripts/ops/run_gensite_adapter_verify.py \
  --vendors victron,sinosoar,sma \
  --write-results \
  --output-csv /tmp/gensite_verify.csv \
  --output-json /tmp/gensite_verify.json
```

Notes:
- Requires `DATABASE_URL` and `CC_CREDENTIAL_ENCRYPTION_KEY` in env.
- `--write-results` updates `site_credentials.last_verified_*`.
- SMA now verifies against live auth/API and can return live flow values.

## OEM integration checklist

1. Commission site/equipment under `/gensite/commission`.
2. Add vendor credential row and run immediate verify.
3. Run bulk verification command for the vendor.
4. Confirm poller writes to `inverter_readings` for that site/vendor.
5. Validate `/gensite/{SITE_CODE}` shows normalized channels (`PV`, `Load`,
   `Battery`, `SoC`, `Grid`, optional `Genset`, and energy counters).
6. Update mapping/tracker docs with confirmed device SN/site-code associations.

## SMA residual gaps

- `fetch_alarms()` is still conservative/no-op.
- Current live mapping uses the latest `energybalance` point for OEM-neutral
  channels (`pv_kw`, `ac_kw`, `battery_kw`, `battery_soc_pct`, `grid_kw`).
- `fetch_day()` now maps each `energybalance.detail` point into normalized
  interval readings with UTC timestamps.
