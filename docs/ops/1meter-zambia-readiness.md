# 1Meter Zambia OTA and commissioning readiness

**Status:** Zambia database/API infrastructure installed; field customer and
payment activation remains gated by authoritative Zambia operating inputs.

Zambia must use the same sealed-gateway workflow as Lesotho and Benin:

1. Keep the OEM factory enclosure sealed. Do not use USB.
2. Put the operator laptop and factory gateway on the recognized provisioning LAN.
3. In Customer Care, select the destination Zambia site and enter that site's Starlink SSID and password.
4. Allocate a stable `<SITE>-GW-####` AWS IoT identity and deliver the certificate plus runtime Wi-Fi configuration through the local provisioning station.
5. Run one signed v1.1.57 OTA canary and wait for a verified `SUCCEEDED` result.
6. Address the meters, connect the sorted RS-485 chain, and reconcile live gateway/meter identities.
7. Assign the meter to an onboarded test customer.
8. Run the recommended physical batch validation: real load, consumption, balance depletion, relay open, synthetic payment, and relay close.
9. Approve a batch only after the evidence is recorded in Customer Care.

## Activation gates

The workflow remains fail-closed until all of the following are complete:

- [ ] The authoritative Zambia site roster is published by the PR/Ops source of truth, including stable three-letter site codes and names.
- [ ] `country_config.py` contains those sites and their districts.
- [x] `onepower_zm` exists and has all current migrations (62 SQL files through `060`).
- [x] `1pdb-api-zm` has a dedicated fail-closed environment (`COUNTRY_CODE=ZM`, port 8103, its own `DATABASE_URL`).
- [x] Caddy routes `/api/zm/*` to port 8103. Production health is a required deployment check.
- [ ] The CC country selector can load Zambia configuration without falling back to another country.
- [ ] The real Zambia tariff, metering platform, payment provider, and mobile-money message formats are approved.
- [ ] `ONEPDB_ZM_SITE_PREFIXES` on the ingestion Lambda contains only the confirmed Zambia site codes.
- [ ] `ota_releases.json` contains an immutable, canary-only v1.1.57 entry for each confirmed Zambia site.
- [ ] At least one controlled factory-v1.1.56 gateway is registered as a Zambia test Thing and explicitly allowlisted for OTA and relay validation.
- [ ] Site or mirrored Starlink credentials are available for the canary.

Do not copy Benin or Lesotho site codes, payment credentials, customer data, or database settings into Zambia. A missing Zambia prerequisite must produce a visible readiness failure rather than silently routing to another country.

The Zambia process environment intentionally contains no Lesotho/Benin Koios,
ThunderCloud, mobile-money, SMS, or bridge credentials. Automatic payment
ingestion, meter crediting, relay automation, and payment receipts are disabled.
The tariff is `0`, so electricity transactions are rejected until an approved
ZMW/kWh tariff is configured.

## Current source-of-truth gap

As of 2026-07-29, the PR portfolio service contains the `1PWR Zambia` organization but exposes no Zambia `siteIds`. Engineering design repositories contain candidate Zambia projects, but they are not a substitute for an approved operational site roster. Confirm the roster before adding OTA catalog keys or telemetry routing prefixes.

## Configuration after the roster is approved

For every approved site code:

1. Add the site to `ZAMBIA.site_abbrev` and `ZAMBIA.site_districts`.
2. Add a matching entry to `ota_releases.json`, initially with `"canary_only": true`.
3. Add the code to the Lambda's comma-separated `ONEPDB_ZM_SITE_PREFIXES`.
4. Confirm Thing names use `<SITE>-GW-####`.
5. Run the CC readiness check before provisioning any device.

The v1.1.57 binary may be shared across countries because site Wi-Fi credentials are runtime NVS configuration. Firmware version and Wi-Fi configuration version must still be tracked separately.
