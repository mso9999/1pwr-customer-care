# Gensite commissioning & inverter telemetry

> **Status:** Migration, credential store, commission wizard, poller path, and
> core adapters are live for Victron, Deye (Solarman backend), Sinosoar, and
> AlphaESS, and SMA Sunny Portal.

## Scope

Commissioning a generation site from CC is a two-thing operation:

1. **Declare the installed equipment** — one row in `site_equipment` per
   inverter / BMS / battery / PV meter. Captures vendor, model, serial,
   nameplate, commissioning date.
2. **Store the vendor backend credentials** — one row in `site_credentials`
   per `(site, vendor, backend)`. Secrets are encrypted at rest with Fernet
   (see `gensite-credentials.md`). The adapter's `verify()` is called before
   the row is saved so we never persist a credential we can't prove works.

Once both are in place, the gensite poller (Phase 1 step 7) will populate
`inverter_readings` and `inverter_alarms`, which the `/gensite/{code}` page
reads directly from 1PDB. Browsers **never** see credentials.

## Vendor coverage (May 2026)

| Vendor | Sites | Backend | Adapter status |
|---|---|---|---|
| Victron | GBO (BN) | VRM Portal REST API | **ready** |
| Deye | LSB (LS), SAM (BN) and BN portfolio logger rollout | Solarman OpenAPI v2 | **ready** |
| Sinosoar | LS minigrids on Sinosoar cloud | sinosoarcloud.com (JSON XHR scrape + CAPTCHA OCR) | **ready** |
| SMA | 7 PIH health centres (LS) | Sunny Portal Keycloak + UI API (`energybalance`) | **ready** |
| AlphaESS | pilot/expanding | sgcloud.alphaess.com API | **ready** |

## Commissioning a new site (operator flow)

1. Navigate to **Ops → Generation Sites → Commission site**
   (`/gensite/commission`).
2. **Site**: paste the UGP project key as the site code so CC and UGP stay
   aligned (PIH clinics use the same UGP code; there is no separate PIH
   namespace). Pick country + kind. Display name matches UGP.
3. **Installed equipment**: one row per device. Serial is optional but
   strongly recommended — we use `(site_code, vendor, serial)` as the upsert
   key, which makes future replace-in-place clean.
4. **Vendor credentials**: the form shows one credential block per distinct
   vendor in the equipment list. Paste the existing portal login 1PWR
   already uses (per the plan, we're not minting new accounts for this —
   just migrating what exists into the encrypted store). Leave
   `site_id_on_vendor` blank for auto-discovery; the Victron adapter, for
   example, will list all installations visible to the login and suggest the
   right `idSite`.
5. Submit. Each credential is `verify()`'d immediately; results are shown
   inline and persisted as `last_verified_at` / `last_verified_ok`.

## Linking from UGP

The stable URL pattern is:

```
https://cc.1pwrafrica.com/gensite/{SITE_CODE}
```

No auth handoff, no query params required. A `?return_to={ugp_url}` query
param renders a **Back to UGP** button. UGP can add this link on the
powerhouse element of any project that has a CC-commissioned gensite.

## Endpoints

| Method | Path | Role |
|---|---|---|
| GET  | `/api/gensite/vendors` | employee |
| GET  | `/api/gensite/sites?country=...` | employee |
| GET  | `/api/gensite/sites/{code}` | employee |
| GET  | `/api/gensite/sites/{code}/live` | employee |
| POST | `/api/gensite/commission` | superadmin / onm_team |
| POST | `/api/gensite/sites/{code}/credentials/{vendor}/{backend}/verify` | employee |
| POST | `/api/gensite/sites/{code}/credentials/{vendor}/{backend}/rotate` | superadmin / onm_team |

Every write path records a `cc_mutations` row with `metadata.kind` of
`site_commission` or `site_credential_rotate`.

## What doesn't exist yet

- **SMA alarms**: `verify`, `fetch_live`, and `fetch_day` are implemented.
  `fetch_alarms` remains conservative/no-op until a stable timestamped SMA
  alarm endpoint is confirmed.
- **Alarms**: `inverter_alarms` table exists; `fetch_alarms()` is stubbed
  on every adapter. Wired into WhatsApp (CC phone + `1PWR LS - OnM Ticket
  Tracker` group) via the existing `cc_bridge_notify.py` helper in Phase 2.
- **O&M ticket creation from alarms** — Phase 2.
- **Historical charts** (`/api/gensite/sites/{code}/series`) — Phase 2.
- **FR i18n** for the commission wizard — Phase 2 (EN-only for now).

## Migration

Schema lives in `acdb-api/migrations/013_gensite_equipment.sql`. Like all
`010+` migrations, it runs automatically on push-to-main via the deploy
workflow (executed as `postgres` against both `onepower_cc` and
`onepower_bj` when those databases exist).

## Related

- `docs/ops/gensite-credentials.md` — encryption key management + rotation SOP
- `CONTEXT.md` → Metering Architecture — for the customer-side telemetry
  counterpart to all of this (SparkMeter / ThunderCloud / 1Meter)
- `acdb-api/sync_ugridplan.py` — reused `UGPClient` if/when gensite
  commissioning wants to push `Comm_Date` back to a UGP powerhouse element
