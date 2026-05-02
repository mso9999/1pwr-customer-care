# Odyssey Standard API

> **Status**: Phase 1 ready for validator. Phase 2 (Zambia backend) tracked separately.
> See [Odyssey validator](https://platform.odysseyenergysolutions.com/#/standard-api/validator).

The Customer Care backend exposes a tag-scoped, bearer-authenticated **pull
API** that the Odyssey monitoring platform calls to ingest electricity
payment and meter-metric records for a funder program. The first consumer
is the **UEF Zambia Energy Demand Stimulation Incentive (ZEDSI)** program.

## Architecture summary

| Concept | Where it lives |
|---|---|
| Funder program registry | [`programs`](../acdb-api/migrations/017_programs_and_odyssey.sql) table |
| Account → program tagging | [`program_memberships`](../acdb-api/migrations/017_programs_and_odyssey.sql) table |
| Bearer-token store | [`odyssey_api_tokens`](../acdb-api/migrations/017_programs_and_odyssey.sql) table |
| Public API | [`acdb-api/odyssey_api.py`](../acdb-api/odyssey_api.py) → mounted at `/api/odyssey/v1/*` |
| Admin CRUD + bulk tagging | [`acdb-api/programs.py`](../acdb-api/programs.py) → mounted at `/api/admin/programs/*` (superadmin) |
| Admin UI | [`Programs page`](../acdb-api/frontend/src/pages/ProgramsPage.tsx) at `/admin/programs` |

Each token is bound to one *(program, country)* pair. The router only ever
returns rows for accounts tagged into that program's
`program_memberships`. There is no cross-program leakage even on the same
backend.

## Endpoints

### `GET /api/odyssey/v1/health`

Public liveness probe — no auth.

```json
{
  "status": "ok",
  "service": "odyssey-standard-api",
  "version": "1.0",
  "timestamp": "2026-04-30T11:00:00+00:00",
  "active_programs": 1
}
```

### `GET /api/odyssey/v1/electricity-payment`

Bearer-token authenticated. Returns paginated payment transactions for
in-program accounts in the requested window.

| Query param | Required | Notes |
|---|---|---|
| `from` | yes | ISO-8601 inclusive lower bound |
| `to`   | yes | ISO-8601 exclusive upper bound. `(to - from)` must be ≤ `ODYSSEY_MAX_WINDOW_HOURS` (default **25h**, env-tunable) |
| `page` | no | default `1` |
| `page_size` | no | default `500`, max `1000` |

Response (truncated):

```json
{
  "dataset": "electricity-payment",
  "program": "UEF_ZEDSI",
  "country": "ZM",
  "from": "2026-04-29T00:00:00+00:00",
  "to":   "2026-04-30T00:00:00+00:00",
  "page": 1, "page_size": 500, "total": 137, "count": 137,
  "next_page": null,
  "data": [
    {
      "external_id": "08D4LT8BWS57",
      "transaction_id": 2461798,
      "timestamp": "2026-04-29T17:58:06+00:00",
      "amount": 100.0,
      "currency": "ZMW",
      "kwh_value": 18.42,
      "payment_type": "mobile_money",
      "source": "mpesa",
      "payment_reference": "08D4LT8BWS57",
      "agent_id": null,
      "customer_id": "12345",
      "customer_name": "Mosa Lephoto",
      "customer_phone": "266...",
      "account_number": "0252SHG",
      "meter_serial": "SMRSD-03-0001B57D",
      "site_id": "SHG",
      "latitude": -29.9,
      "longitude": 28.7
    }
  ]
}
```

### `GET /api/odyssey/v1/meter-metrics`

Same auth + window rules. Returns daily kWh roll-ups per *active meter*
per account. `error_type` is `"normal"` when at least one hourly reading
existed for that day, else `"offline"`.

```json
{
  "external_id": "SMRSD-03-0001B57D-2026-04-29",
  "timestamp": "2026-04-29T00:00:00+00:00",
  "interval": "P1D",
  "kwh_delivered": 4.13,
  "reading_count": 24,
  "error_type": "normal",
  "meter_serial": "SMRSD-03-0001B57D",
  "account_number": "0252SHG",
  "site_id": "SHG",
  "customer_id": "12345",
  "customer_name": "Mosa Lephoto",
  "latitude": -29.9,
  "longitude": 28.7
}
```

### Auth errors

- `401 Missing or malformed Authorization header` — no Bearer header
- `401 Invalid bearer token` — token hash not in `odyssey_api_tokens`
- `401 Token has been revoked` — `revoked_at` is set
- `401 Token has expired` — `expires_at <= now()`
- `403 Program is inactive` — `programs.active = false`

## Field mapping vs. Odyssey internal names

The **Odyssey Connections claim spreadsheet** ([`docs/uef_zedsi_claim_template.xlsx`](./uef_zedsi_claim_template.xlsx))
uses internal field IDs in its `parsingHeaders` row. The API output is
aligned semantically with these so Odyssey ingestion is straightforward:

| Odyssey field (`parsingHeaders`) | API response field | Source |
|---|---|---|
| `customer.name`              | `customer_name`           | `customers.first_name` + `last_name` |
| `customer.phoneNumber`       | `customer_phone`          | `customers.phone` ∨ `cell_phone_1` |
| `customer.governmentIdNumber`| (set on Connections upload only) | `customers.national_id` |
| `customer.gender`            | (Connections upload)      | `customers.gender` |
| `customer.simpleCategory`    | (Connections upload)      | `customers.simple_category` (new col, migration 017) |
| `customer.type`              | (Connections upload)      | `customers.customer_type` |
| `customer.locationDistrict`  | (Connections upload)      | `customers.district` |
| `customer.locationAddress`   | (Connections upload)      | `customers.street_address` |
| `customer.latitude`          | `latitude`                | `customers.gps_lat` |
| `customer.longitude`         | `longitude`               | `customers.gps_lon` |
| `remoteId`                   | `meter_serial`            | resolved from `meters` |
| `connected`                  | (Connections upload)      | `customers.date_service_connected` |
| `energySource`               | (Connections upload)      | `customers.previous_energy_source` (new col, migration 017) |

The API endpoints carry the **continuous monitoring** signal; the
spreadsheet carries the **one-time claim** record (PUE equipment, demand
stimulation activities, etc.). Both are scoped by the same
`program_memberships` table.

## Operations

### Apply migration

```bash
sudo -u postgres psql -d onepower_cc -f /opt/cc-portal/backend/migrations/017_programs_and_odyssey.sql
# Repeat for onepower_bj if BN should expose the same table set.
# Future: same migration runs on onepower_zm when ZM backend is stood up.
```

### Tagging the ZEDSI cohort

Through the **Programs admin UI** (`/admin/programs`, superadmin only):

1. Pick the program (`UEF_ZEDSI` is seeded).
2. **Bulk tag accounts** — paste country codes, site codes, or individual
   account numbers. The action upserts memberships idempotently and reports
   `affected_count` + `skipped_unknown` (account numbers that do not exist
   in `accounts`).
3. **Issue an Odyssey API token** — labelled, optional expiry (default 90
   days). The plaintext is shown **once**. If lost, revoke and reissue.

Same flows are scriptable via the API:

```bash
# Tag all SHG site customers as ZEDSI Milestone 1
curl -X POST -H "Authorization: Bearer $CC_JWT" -H 'Content-Type: application/json' \
  https://cc.1pwrafrica.com/api/admin/programs/UEF_ZEDSI/memberships/bulk \
  -d '{"action":"add","site_codes":["SHG"],"claim_milestone":"Milestone 1"}'

# Issue a 90-day token
curl -X POST -H "Authorization: Bearer $CC_JWT" -H 'Content-Type: application/json' \
  https://cc.1pwrafrica.com/api/admin/programs/UEF_ZEDSI/tokens \
  -d '{"label":"odyssey-uef-prod","lifetime_days":90}'
```

### Sandbox-validator workflow (run BEFORE wiring the real ZEDSI customer set)

1. Create a sandbox program via the UI:
   - Code: `UEF_ZEDSI_TEST`
   - Country: `LS` (so we can exercise it against existing data)
2. Bulk-tag a few real Lesotho accounts (e.g. `0001MAK,0045MAK,0252SHG`) into the sandbox program.
3. Issue a sandbox token.
4. Run a smoke check:

   ```bash
   TOKEN='ody_...'
   FROM='2026-04-29T00:00:00Z'
   TO='2026-04-30T00:00:00Z'
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://cc.1pwrafrica.com/api/odyssey/v1/health"
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://cc.1pwrafrica.com/api/odyssey/v1/electricity-payment?from=$FROM&to=$TO" | jq '.total, .data[0]'
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://cc.1pwrafrica.com/api/odyssey/v1/meter-metrics?from=$FROM&to=$TO" | jq '.total, .data[0]'
   ```

5. Open the Odyssey validator at <https://platform.odysseyenergysolutions.com/#/standard-api/validator>:
   - Dataset type: **Electricity payment** → enter `https://cc.1pwrafrica.com/api/odyssey/v1/electricity-payment` and the sandbox token. Run.
   - Re-run with **Meter metrics** → `https://cc.1pwrafrica.com/api/odyssey/v1/meter-metrics`.
6. If the validator reports field-shape mismatches, refine `_format_payment_record` / `_format_meter_metric_record` in [`acdb-api/odyssey_api.py`](../acdb-api/odyssey_api.py) and redeploy. Capture the validator output in this file under "Validator history" for future reference.
7. When both datasets pass, **revoke the sandbox token** (`DELETE /admin/programs/UEF_ZEDSI_TEST/tokens/{id}`) and remove the sandbox program if no longer needed.

### Token rotation

- Recommended cadence: **90 days**.
- Issue new token first → coordinate hand-off with Odyssey support → revoke the old token.
- Tokens are uniquely identified in the UI by their **prefix** (`ody_xxxxxxx…`); the plaintext is never persisted.

### Operational checks

```bash
# Ensure the active token is healthy
curl -s -H "Authorization: Bearer $TOKEN" https://cc.1pwrafrica.com/api/odyssey/v1/health | jq

# What did Odyssey hit in the last hour?
journalctl -u 1pdb-api --since '1 hour ago' | grep odyssey

# Token usage trail
sudo -u postgres psql -d onepower_cc -c \
  "SELECT id, label, token_prefix, last_used_at, last_used_ip
     FROM odyssey_api_tokens ORDER BY last_used_at DESC NULLS LAST LIMIT 20;"
```

## Configuration

Environment variables (set on the country's API host, e.g. `1pdb-api`,
`1pdb-api-bn`, future `1pdb-api-zm`):

| Var | Default | Notes |
|---|---|---|
| `ODYSSEY_MAX_WINDOW_HOURS` | `25` | Maximum `(to − from)` window. The MPM Odyssey reference uses 24h; we add an hour of slack. Tighten or relax based on validator feedback. |

No new secrets are required — the backend reads tokens from its own DB.

## Open items / validator history

> Append entries here after each validator run so the next person knows
> what shape Odyssey is currently happy with.

- **2026-04-30** — Phase 1 shipped. Initial payload conforms to the
  MicroPowerManager Odyssey integration shape; awaiting first validator run
  against the sandbox program.
