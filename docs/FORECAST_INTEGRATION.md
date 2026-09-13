# Forecast integration (uGridPREDICT ← Customer Care)

Read-only machine API. Does **not** change field CC workflows, UGP sync, Odyssey,
site ingest, or existing `/api/integration/om/*` / `/doe/*` payloads.

## Auth

Same gate as Nexus Reports:

- Header: `X-CC-Integration-Key`
- Env: `CC_INTEGRATION_KEY` on the CC host
- Unset key → `503` (fail closed). Wrong key → `401`.

uGridPREDICT must not use an employee JWT.

## Canonical measure

Lender- and board-facing connections = **`customer_commissioned`** month-end stock.

Do not substitute cash-model openings, DoE letter counts, UGP `St_code_3 >= 9`,
or `date_service_connected`. Those siblings are returned only when named.

## Join key

`site_code` is the **3-letter** PR / CC community code:

`customers.community` = `site_metadata.site_code` = PR `referenceData_sites` code.

Catalog: `GET /api/integration/forecast/sites`

## Endpoints

All accept optional `site` (`MAS`), `date_from`, `date_to` (`YYYY-MM`). Default
window is the last 36 months through the current month. Lane currency and
country come from the CC process (LS vs BN are separate hosts).

| Method | Path | Series |
|--------|------|--------|
| GET | `/api/integration/forecast/sites` | Join-key catalog |
| GET | `/api/integration/forecast/connections` | `customer_commissioned` stock + named siblings `service_connected`, `commissioned_missing_date` |
| GET | `/api/integration/forecast/consumption` | `kwh`, `kwh_per_connection` (kWh / commissioned stock). No ARPU |
| GET | `/api/integration/forecast/collections` | `billed_local`, `collected_local`. Prepaid billed = cash collected; invoiced rows added when `invoiced_revenue` exists. `arpu` is always `null` |

## What this API does not do

- Write CC
- Treat UGP milestones as commissioned
- Change `/api/integration/om/overview` (that route still uses service-connected counts for Nexus Reports)
