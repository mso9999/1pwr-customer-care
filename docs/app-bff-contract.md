# Mobile app BFF contract (`/api/app/*`)

> Public, unauthenticated endpoints consumed by the 1PWR mobile app
> (`1PWRBENIN-v2` / Flutter package `mionwa`). Implementation lives in
> [`acdb-api/app_bff.py`](../acdb-api/app_bff.py); tests in
> [`acdb-api/tests/test_app_bff.py`](../acdb-api/tests/test_app_bff.py).

This file is the **single source of truth** for the mobile contract. The
Flutter client (`lib/core/config/country_registry_client.dart`) and the
Python server must agree on the shapes documented here. Update this file
in the same PR as any contract change, then mirror the change in the app
repo.

## Host

- Production: `https://cc.1pwrafrica.com`
- The shared list is served by the LS instance (`1pdb-api`) on port 8100;
  Caddy reverse-proxies `/api/*` to it. Both LS and BN instances import
  the same `country_config._REGISTRY`, so either can answer.

## Authentication

None for the routes in this document — they are read-only catalog data.
CORS is wildcarded in `customer_api.py` so the Flutter web build also
works.

## Versioning

The contract is unversioned for now; add new fields as **optional** and
treat removals or renames as breaking. The Flutter client already accepts
both `snake_case` and `camelCase` for legacy reasons (see
`country_registry_client.dart`); new fields should be **camelCase** to
match what the server emits.

---

## `GET /api/app/active-countries`

Returns the countries the mobile app may select on its country-picker
screen.

### Request

```
GET /api/app/active-countries HTTP/1.1
Accept: application/json
```

No query parameters. No headers required beyond `Accept`.

### Response — 200 OK

```json
{
  "countries": [
    {
      "countryCode": "LS",
      "displayName": "Lesotho",
      "active": true
    },
    {
      "countryCode": "BN",
      "displayName": "Bénin",
      "active": true
    }
  ]
}
```

Headers:

```
Content-Type: application/json
Cache-Control: public, max-age=300
```

### Row schema

| Field          | Type     | Required | Notes |
|----------------|----------|----------|-------|
| `countryCode`  | string   | yes      | ISO 3166-1 alpha-2, uppercase. |
| `displayName`  | string   | yes      | User-facing label. May contain non-ASCII (e.g. `Bénin`). Server falls back to `CountryConfig.name` when `display_name` is unset. |
| `active`       | boolean  | yes      | Always `true` in the response — inactive rows are filtered server-side. The field is kept for forward compatibility with clients that re-filter. |
| `appConfigUrl` | string   | no       | HTTPS URL pointing at a full Flutter `CountryConfig` JSON pack (matching `1PWRBENIN-v2/assets/config/country_bn.json`). When present, the app loads this instead of its bundled asset. **Advertised for every country that has a pack in `acdb-api/app_packs.py`** (BN and LS today); set the code to `""` in `_REMOTE_CONFIG_URLS` to suppress it while staging. |

### Server-side filtering rules

- Rows with `CountryConfig.active=False` are excluded.
- Codes are returned in the registry's insertion order
  (`country_config._REGISTRY`); the app sorts as needed for UI.

### Client behaviour (for reference)

The Flutter client (`CountryRegistryClient.fetchActiveCountries`):

- Times out after 20 s and falls back to the bundled BN/LS catalog.
- Skips any row without a recognised code or, when `appConfigUrl` is
  absent, without a bundled asset for that code (see
  `country_bundled_paths.dart`).
- Treats `active: false` as "skip" even though the server already
  filters — this is belt-and-braces for future server changes.

### Error responses

The endpoint has no failure modes beyond infrastructure errors. CC
should never return 4xx for this route. On 5xx, the app falls back to
its bundled catalog and shows a "retry" affordance on the picker.

---

## Post-deploy smoke runbook

After deploying CC with this endpoint:

1. **Backend reachability** (any host):
   ```bash
   curl -fsSL https://cc.1pwrafrica.com/api/app/active-countries | jq .
   ```
   Expect HTTP 200, body shaped per the schema above, `Cache-Control` header present.

2. **Flutter app — server-driven path** (from `1PWRBENIN-v2`):
   ```bash
   flutter run -d <device>
   ```
   Default `--dart-define=COUNTRY_REGISTRY_URL` already points at the
   production endpoint. On first launch, the picker should list both
   `Lesotho` and `Bénin` (sourced from CC, not the bundled fallback).

3. **Flutter app — fallback path** (verify resilience):
   ```bash
   flutter run --dart-define=COUNTRY_REGISTRY_URL=https://localhost:1/none
   ```
   Picker still shows BN + LS from
   `lib/core/config/country_catalog.dart#bundledFallback`.

## Operations notes

- **Adding a country:** edit `acdb-api/country_config.py` (new
  `CountryConfig` entry, register in `_REGISTRY`). Set `active=False`
  to stage it without exposing it to the app. Bump cache TTL only if
  needed; default is 5 min.
- **Toggling a country off:** set `active=False` on its
  `CountryConfig`. Picker drops it within `max-age` (≤ 5 min).
- **Promoting to a database-backed registry:** see "Later" in the
  cross-repo plan; this contract is stable across that swap.

## `GET /api/app/country-config/{code}`

Returns the Flutter-shaped `CountryConfig` JSON pack for one country. The
app loads this instead of its bundled asset when `/active-countries`
advertises an `appConfigUrl` for that code (which it now does for every
country with a pack). The schema is a **superset** of
`1PWRBENIN-v2/assets/config/country_bn.json`; new fields are optional so
older app builds keep working.

### Request

```
GET /api/app/country-config/BN HTTP/1.1
Accept: application/json
```

No auth. Path parameter `code` is case-insensitive (upper-cased server-side).

### Response — 200 OK

```json
{
  "countryCode": "BN",
  "displayName": "Bénin",
  "apiBaseUrl": "https://app.onepowerbenin.com/api",
  "requestTimeoutSeconds": 30,
  "localeTag": "fr_FR",
  "currencyCode": "XOF",
  "appTitle": "1PWR",
  "features": { "momo": true, "meterLan": true, "messaging": true, "startingKit": true },
  "paymentProviders": [
    { "id": "mtn_momo", "displayName": "MTN MoMo", "iconAsset": "assets/images/mtnlogo.png", "apiMethod": "MTN MoMo" },
    { "id": "orange_money", "displayName": "Orange Money", "iconAsset": "assets/images/orangelogo.png", "apiMethod": "Orange Money" }
  ],
  "paymentPaths": {
    "momoPrefix": "momo",
    "initiate": "momo/initiate",
    "statusPrefix": "momo/status",
    "recharger": "recharger",
    "historyLastThree": "momo/history/last-three",
    "historyLastFive": "momo/history/last-five"
  },
  "meterLan": {
    "softApSsidPrefixes": ["1PWR", "ONEMETER", "MESH"],
    "localApiBasePath": "/v1",
    "mdnsHost": "onemeter.local"
  },
  "kwhDivisor": 160,
  "tariffRate": 160,
  "quickRechargeAmounts": [1000, 2000, 5000],
  "zones": [
    { "code": "GBO", "name": "Gbowele" },
    { "code": "SAM", "name": "Samionta" }
  ],
  "fees": {
    "onboardingFee": 10000,
    "startingKitFee": 40000,
    "connectionFee": 0,
    "readyboardFee": 0,
    "currency": "XOF"
  }
}
```

Headers:

```
Content-Type: application/json
Cache-Control: public, max-age=300
```

### Field schema

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `countryCode` | string | yes | ISO 3166-1 alpha-2, uppercase. |
| `displayName` | string | yes | User-facing label (may be non-ASCII). |
| `apiBaseUrl` | string | yes | HTTPS API root without trailing slash. |
| `requestTimeoutSeconds` | int | no | Default 30. |
| `localeTag` | string | no | BCP-47-ish locale tag, e.g. `fr_FR`. |
| `currencyCode` | string | yes | ISO 4217. |
| `appTitle` | string | no | App-bar title. |
| `features` | object | yes | Feature flags (see below). |
| `paymentProviders[]` | array | yes | Selectable providers; `apiMethod` is the value sent as `methode` in recharge/starting-kit requests. |
| `paymentPaths` | object | yes | Relative paths under `apiBaseUrl`. |
| `meterLan` | object | no | Meter SoftAP hints; omit to disable meter-LAN. |
| `kwhDivisor` | number | no | balance ÷ divisor = kWh. Derived from `tariffRate`; the app should prefer `kwhDivisor`. |
| `tariffRate` | number | no | Currency per kWh (live `system_config` override of `CountryConfig.default_tariff_rate`). |
| `quickRechargeAmounts[]` | array | no | Quick-add recharge buttons. |
| `zones[]` | array | no | `{ code, name }` concession list for auth + onboarding pickers. |
| `fees` | object | no | `onboardingFee`, `startingKitFee`, `connectionFee`, `readyboardFee`, `currency`. Live values read from `system_config` (editable via `/api/admin/country-fees`); fall back to `CountryConfig` / pack defaults. |

`features` keys: `momo`, `meterLan`, `messaging`, `startingKit` (booleans).

### Live provisioning

- **Fees & tariff:** `connectionFee`, `readyboardFee`, and `tariffRate` are
  read from `system_config` (`connection_fee_amount`,
  `readyboard_fee_amount`, `tariff_rate`) — the same rows
  `/api/admin/country-fees` writes — so finance/O&M edits surface in the
  app within the 5-minute cache TTL. `onboardingFee` / `startingKitFee`
  read from `onboarding_fee_amount` / `starting_kit_fee_amount` when
  present, falling back to pack defaults.
- **Providers, paths, flags, zones, quick amounts, API URL:** declared in
  `acdb-api/app_packs.py`; change on deploy. (DB-backed provisioning is a
  later swap; the contract is stable across it.)
- When the DB is unavailable, the endpoint falls back to static defaults
  so the app still boots.

### Error responses

- `404` — no pack registered for `code` (e.g. `ZM` today).
- `5xx` — infrastructure error; the app falls back to its bundled asset
  for that code.

## `POST /api/app/auth/session` (auth bridge)

Exchange a verified client code + PIN for a CC customer JWT. The app then
sends the JWT as `Authorization: Bearer <token>` to the dashboard /
transactions / fees routes below.

PIN/`check-client` live in the per-country legacy API (`apiBaseUrl`), not
CC. The bridge proxies `POST {apiBaseUrl}/pin/verify` to that legacy API;
on `success: true` it normalises the client code to a CC account number
(`auth.normalize_account_number`), validates it in 1PDB when present
(`auth._validate_account_exists`), and mints a short-lived customer JWT
(`middleware.create_token`, TTL `CC_JWT_EXPIRY_HOURS`, default 8h). If the
account is not yet in 1PDB (e.g. newly onboarded, ingest not backfilled),
a token is still minted scoped to the normalised account.

### Request

```json
{ "client_code": "0001SAM", "pin": "1234" }
```

`client_code` is case-insensitive and accepts `0001SAM` or `SAM0001`.

### Response — 200 OK

```json
{
  "access_token": "eyJ...",
  "expires_in": 28800,
  "client": { "code": "0001SAM", "name": "Sam Sample" }
}
```

### Errors

- `400` — missing `client_code` or `pin`.
- `401` — legacy `pin/verify` returned `success: false` (bad credentials).
- `502` — legacy API unreachable / errored.

## `GET /api/app/dashboard` (JWT)

Customer dashboard — reuses `crud.my_dashboard` verbatim (same data as the
web portal `/api/my/dashboard`), augmented with `fee_debt` + `financing`
snapshots so the app can render the fees/debt panel without a second call.

### Response (subset)

```json
{
  "balance_kwh": 12.5,
  "balance_currency": 2000.0,
  "currency_code": "XOF",
  "last_payment": { "amount": 5000, "date": "2026-06-30 ...", "kwh_purchased": 31.25 },
  "avg_kwh_per_day": 2.1,
  "estimated_recharge_seconds": 514285,
  "total_kwh_all_time": 1240.0,
  "total_lsl_all_time": 198400.0,
  "daily_7d": [{ "date": "2026-06-26", "kwh": 2.0 }],
  "daily_30d": [{ "date": "2026-06-03", "kwh": 1.8 }],
  "monthly_12m": [{ "month": "2026-07", "kwh": 60.0 }],
  "meters": [{ "meter_id": "M-001", "platform": "sparkmeter", "role": "main", "status": "active" }],
  "meter_comparison": {},
  "hourly_24h": [],
  "fee_debt": { "connection_remaining": 5000.0, "readyboard_remaining": 0.0, "total_remaining": 5000.0, "commissioned": true },
  "financing": { "has_financing": false, "total_outstanding": 0.0, "active_agreements": 0, "repayment_fraction": 0.0 }
}
```

`Cache-Control: no-store` (per-customer, live).

## `GET /api/app/transactions` (JWT)

Paginated transaction ledger with fee/advance/financing/electricity split.
Mirrors the employee `customer-data` query, scoped to the JWT account.

### Query params

`limit` (1–200, default 50), `offset` (≥ 0).

### Response

```json
{
  "account": "0001SAM",
  "total": 137,
  "limit": 50,
  "offset": 0,
  "transactions": [
    {
      "id": 9001,
      "account": "0001SAM",
      "meter": "M-001",
      "date": "2026-06-30 14:00:00",
      "amount": 5000.0,
      "rate": 160.0,
      "kwh": 31.25,
      "is_payment": true,
      "balance": 12.5,
      "fee_repayment_portion": 2500.0,
      "advance_portion": 0.0,
      "financing_portion": 0.0,
      "electricity_portion": 2500.0,
      "payment_reference": "MP240630140000"
    }
  ]
}
```

Split columns fall back to `0.0` / `null` on older schemas that predate
migration `029_customer_fee_debt.sql`. `Cache-Control: no-store`.

## `GET /api/app/fees` (JWT)

Fee schedule + current debt + split policy for the signed-in customer.

### Response

```json
{
  "account": "0001SAM",
  "currency": "XOF",
  "tariff_rate": 160.0,
  "schedule": {
    "connection_fee": 10000.0,
    "readyboard_fee": 40000.0,
    "low_balance_kwh_threshold": 5.0,
    "low_balance_kwh_clear": 12.0
  },
  "fee_debt": { "connection_remaining": 5000.0, "readyboard_remaining": 0.0, "total_remaining": 5000.0, "commissioned": true },
  "financing": { "has_financing": false, "total_outstanding": 0.0, "active_agreements": 0, "repayment_fraction": 0.0 },
  "split_policy": {
    "fee_cap_fraction": 0.5,
    "description": "Up to half of each electricity payment goes to fee debt (connection first, then readyboard); the remainder buys energy. Financing is taken from the electricity slice when an active agreement exists.",
    "dedicated_payment_rule": "Payments ending in 1 or 9 are treated as dedicated financing repayments (100% to debt)."
  }
}
```

`Cache-Control: no-store`. Schedule amounts come from `system_config`
(editable via `/api/admin/country-fees`); debt comes from the customer row
(`fee_debt_connection_remaining` / `fee_debt_readyboard_remaining`);
financing from `financing_agreements` (Lesotho; absent → all-zero on Benin).

## `GET /api/app/care/threads` (JWT)

Lists the signed-in customer's care messages as "threads" (one inbound
message = one thread today; replies will extend this later).

### Query params

- `limit` (int, default 50, 1–200)
- `offset` (int, default 0)

### Response — 200 OK

```json
{
  "threads": [
    {
      "id": 12,
      "text": "My meter is offline",
      "category": "fault",
      "source": "app",
      "status": "sent",
      "om_ticket_ref": null,
      "created_at": "2026-07-02T03:00:00+00:00"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`status` is one of `sent` (delivered to the WA bridge / OM), `queued`.
`om_ticket_ref` is populated when the country bridge opens an OM ticket.

## `GET /api/app/care/threads/{id}/messages` (JWT)

Returns the thread detail and its messages. Today the message list
contains the single outbound customer message; agent replies will be
appended in a later phase. Returns 404 if the thread does not belong to
the signed-in customer.

## `POST /api/app/care/messages` (JWT)

Create a care message from the signed-in customer and WA-bridge it to
the country care phone. JWT-gated wrapper over the existing
`app_care_messages` table + `notify_cc_bridge` flow used by
`POST /api/customer/messages`.

### Request

```json
{ "text": "My meter is offline", "category": "fault", "device_id": "abc" }
```

Optional idempotency header `X-Idempotency-Key`. On a duplicate key the
stored row is returned with `"duplicate": true` and the bridge is not
re-notified.

### Response — 200 OK

```json
{ "status": "ok", "id": 12, "duplicate": false }
```

## `POST /api/app/device` (JWT)

Register or refresh an FCM device token for the signed-in customer.

### Request

```json
{ "token": "<fcm registration token>", "platform": "android" }
```

### Response — 200 OK

```json
{ "status": "ok", "id": 7 }
```

Upserts on `(account_number, token)`.

## `GET /api/app/notifications` (JWT)

Paginated inbox for the signed-in customer (newest first).

### Query params

- `limit` (int, default 50, 1–200)
- `offset` (int, default 0)
- `unread_only` (bool, default false)

### Response — 200 OK

```json
{
  "notifications": [
    {
      "id": 12,
      "type": "payment_receipt",
      "title": "1PWR",
      "body": "Paiement 5000 FCFA pour le compte 0001SAM enregistré…",
      "payload": { "amount": 5000, "balance_kwh": 12.4 },
      "created_at": "2026-07-02T03:00:00+00:00",
      "read_at": null,
      "fcm_status": "sent"
    }
  ],
  "total": 1,
  "unread": 1,
  "limit": 50,
  "offset": 0
}
```

`type` mirrors the SMS send point: `payment_receipt`, `fee_receipt`,
`low_balance`, `welcome`. `fcm_status` is one of `sent`, `no_tokens`,
`error`, `pending`.

## `POST /api/app/notifications/read` (JWT)

Mark a single notification (`{"notification_id": 12}`) or all
(`{"all": true}`) as read.

### Response — 200 OK

```json
{ "status": "ok", "updated": 1 }
```

## `DELETE /api/app/notifications/{id}` (JWT)

Delete a notification owned by the signed-in customer. 404 if not found.

### Server-side mirroring

`app_notifications.mirror_to_app(...)` is invoked from
`sms_payment_receipt.py`, `low_balance_alerts.py`, and
`contract_gen.send_contract_sms` so every SMS CC sends today is mirrored
into the inbox and dispatched via FCM. SMS can be discontinued once the
inbox + push are validated.

## `GET /api/app/sandbox/status`

Reports whether sandbox mode is enabled (`APP_SANDBOX=1`).

### Response — 200 OK

```json
{ "enabled": true, "account_number": "0000SBX", "meter_id": "SBX-TEST-0001" }
```

## `POST /api/app/sandbox/seed` (sandbox only)

Creates a dummy customer + account + meter and N synthetic electricity
payments through the same `transactions` shape used by real ingest — no
real payment gateway, no real energy. Returns 404 when sandbox mode is
off.

### Request

```json
{ "payments": 12, "amount": 5000.0, "rate": 160.0 }
```

### Response — 200 OK

```json
{
  "status": "ok",
  "account_number": "0000SBX",
  "pin": "sandbox",
  "meter_id": "SBX-TEST-0001",
  "name": "Sandbox Customer",
  "payments_created": 12
}
```

### Sandbox auth shortcut

When sandbox mode is on, `POST /api/app/auth/session` accepts
`pin == "sandbox"` and mints a CC customer JWT for the (seeded) dummy
account WITHOUT proxying PIN verification to the legacy per-country API.
The response includes `"sandbox": true`. This lets the app log in on an
Android emulator against synthetic data.

### Deployment model — ring-fenced by database, not by code

CC is fully `DATABASE_URL`-driven (every DB access flows through
`customer_api.get_connection()`), so the sandbox does **not** require a
forked or rewritten system. It is the **same CC image** running as a
second instance against a **separate sandbox database** on the same
Postgres cluster, with `APP_SANDBOX=1`. The ring-fenced artifacts are
only:

1. A sandbox database (`onepower_cc_sandbox`), created + migrated in one
   command via `scripts/ops/init_sandbox_db.sh` (reuses the production
   `migrations/apply_migrations.sh`).
2. A sandbox env file (`deploy/sandbox.env.example`) — same code, different
   `DATABASE_URL` + a non-prod `CC_API_PORT` + `APP_SANDBOX=1`.
3. Optionally a second Firebase project for sandbox push, or simply leave
   FCM unset (best-effort no-op; notifications still land in the
   `app_notifications` inbox).

The app targets it via `--dart-define=SANDBOX_API_BASE=http://<sandbox-host>/api`.

**Production-DB guard.** `POST /api/app/sandbox/seed` refuses to run when
`APP_SANDBOX=1` and the active `DATABASE_URL` resolves to a production
database name (`onepower_cc`, `onepower_bj`, `onepower_ls`,
`onepower_zm`). Set `APP_SANDBOX_ALLOW_PROD_DB=1` to override explicitly.
This prevents a misconfigured sandbox env from seeding dummy rows into
production data.

## `GET /api/app/customers/lookup?q=` (JWT)

Recipient lookup for direct customer-to-customer messaging. Accepts an
account number (e.g. `0002SAM`) or a phone number. Returns 404 when no
match, 400 when the lookup resolves to the signed-in customer.

### Response — 200 OK

```json
{ "recipient": { "account_number": "0002SAM", "name": "Jane Doe", "phone": "100000000" } }
```

## `POST /api/app/messages/direct` (JWT)

Send a direct customer-to-customer message. When `mirror_to_whatsapp` is
true and the recipient has a phone, the message is also routed through
the country WhatsApp bridge (`cc_bridge_notify`) to the recipient.

### Request

```json
{ "to_customer": "0002SAM", "body": "Hello!", "mirror_to_whatsapp": true }
```

### Response — 200 OK

```json
{
  "status": "ok",
  "id": 6,
  "to": { "account_number": "0002SAM", "name": "Jane Doe", "phone": "100000000" },
  "delivery_status": "sent"
}
```

`delivery_status` is one of `sent`, `wa_failed` (bridge error),
`wa_no_phone` (mirror requested but recipient has no phone).

## `GET /api/app/messages/direct` (JWT)

Paginated list of direct messages sent by or received by the signed-in
customer (`direction` = `outbound` / `inbound`).

## Future endpoints (placeholders, not yet implemented)

- DB-backed country registry (`_REGISTRY` → Postgres); this contract is
  stable across that swap.

## Phase 6 — acceptance validation runbook (real meters + real payments)

No new CC code is expected here; this is an acceptance test phase that
exercises the full stack against real data via the existing CC ingest /
commission pipeline (`registration.py`, `commission.py`,
`meter_provisioning.py`, `ingest.py`, `momo_bj.py` / `mpesa_sms.py`).

Runbook:

1. Commission a real customer + meter through the existing CC flow
   (`registration` → `commission` → `meter_provisioning`).
2. Make a real mobile-money payment against that account and let
   `ingest.py` commit it.
3. In the app, log in via the PIN auth bridge
   (`POST /api/app/auth/session`) and verify:
   - `GET /api/app/dashboard` reflects the real balance, 7d/30d/12m
     consumption, and meters;
   - `GET /api/app/transactions` shows the real payment with the
     energy / fee / financing split portions;
   - `GET /api/app/fees` shows connection / readyboard / financing
     debt and the split-policy explainer.
4. Trigger a low-balance condition and verify:
   - the existing SMS send still fires (`low_balance_alerts.py`);
   - `app_notifications` gains a `low_balance` row and FCM pushes to
     the registered device token;
   - the in-app Notifications inbox lists it (bell icon on Dashboard).
5. Open a care ticket from the Messages tab and verify the
   `app_care_messages` row is created and the country WhatsApp bridge
   delivers it to the care phone (`POST /api/app/care/messages`).
6. (Optional) Send a customer-to-customer direct message with the WA
   mirror toggle and confirm delivery to the recipient's phone
   (`POST /api/app/messages/direct`).

File app bugs as found; no contract changes are expected unless a bug
surfaces a schema gap.
