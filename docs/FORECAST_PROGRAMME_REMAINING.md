# Remaining work — forecast programme (Customer Care)

**Consumer:** `AI Projects/uGridPREDICT`. This repo owns connections, consumption, tariffs, collections.
There is **no CC adapter** in the forecast service yet. Do not invent a second connection measure.

## Canonical field (non-negotiable)

Anything lender- or board-facing uses **`customer_commissioned`**. Live 7 Sep 2026: **1,388**. Last commission date 5 Aug.

Do **not** substitute:

- cash-model opening 1,678
- DoE 1,553
- UGP physical `St_code_3 >= 9`
- billing-active / contracted / energised — name those explicitly if exposed

## What this agent must do

1. **Service-to-service read** of `customer_commissioned` (and named siblings) by site and month. Today the aggregate routes require an **employee JWT**. The forecast service cannot use a human login. Add an integration-key (or equivalent) read path, same pattern as AM/PR/UGP.
2. **History** — monthly series, not only current state. D1 cannot difference a snapshot against last month’s forecast without it.
3. **Consumption and collections** — kWh/connection by site and customer class; billed vs collected. No blended ARPU for the consumer.
4. Document the join key to PR site codes (3-letter). Register the endpoint in this repo and in `nexus-portal/docs/CANONICAL_DATA_OWNERSHIP.md`.

## Status (13 Sep 2026)

CC side of (1)–(4) is implemented. Contract: `docs/FORECAST_INTEGRATION.md`.

| Path | Auth |
|------|------|
| `GET /api/integration/forecast/sites` | `X-CC-Integration-Key` |
| `GET /api/integration/forecast/connections` | same |
| `GET /api/integration/forecast/consumption` | same |
| `GET /api/integration/forecast/collections` | same |

Still required outside this repo:

- Point uGridPREDICT at these routes (no employee JWT).
- Confirm `CC_INTEGRATION_KEY` is set on the LS CC host (already used by Nexus Reports).
- First consumer check: MAS monthly `customer_commissioned`, then the portfolio.

## What this agent must not do

- Do not change field CC workflows.
- Do not let the forecast service write CC.
- Do not treat UGP milestone `met` as commissioned customers.

## Done when

uGridPREDICT can read, with a non-interactive key, monthly `customer_commissioned`, kWh/connection, billed and collected for MAS (then the portfolio), without an employee JWT.
