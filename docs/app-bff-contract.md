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
| `appConfigUrl` | string   | no       | HTTPS URL pointing at a full Flutter `CountryConfig` JSON pack (matching `1PWRBENIN-v2/assets/config/country_bn.json`). When present, the app loads this instead of its bundled asset. **Omitted in v1.** |

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

## Future endpoints (placeholders, not yet implemented)

- `GET /api/app/country-config/{code}` — return the Flutter-shaped
  `CountryConfig` JSON pack so we can change currency/providers/feature
  flags without an app build. URLs will appear as `appConfigUrl` rows
  in `/active-countries`. Schema must match
  `1PWRBENIN-v2/assets/config/country_bn.json`.
