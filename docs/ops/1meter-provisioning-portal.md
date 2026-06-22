# 1Meter provisioning portal (CC)

GUI + API for bringing a factory-flashed 1Meter online as a canonical, registered
AWS IoT Thing — without AWS credentials or the PowerShell kit on the field laptop.

- **UI:** Portal → **Provisioning** (superadmin / O&M team). `src/pages/ProvisioningPage.tsx`.
- **API:** `acdb-api/meter_provisioning.py`, mounted at `/api/provisioning`.
- **SOP:** `onepwr-aws-mesh/Docs/SOP-1meter-operational-ota-provisioning.md` (primary path).

## Why CC owns it

CC already holds the canonical **site codes** (`country_config.ALL_KNOWN_SITES`)
and customer **accounts**, so the Thing name `<SITE>-<account>` (e.g. `MAK-0026`,
`BEN-0026`) is canonical by construction and can't drift into ad-hoc `TestSite*`
names. The DynamoDB provisioning registry (`1meter_provisioning_registry`) stays
the single source of truth for PCB-MAC → Thing, shared with the bench/HQ
PowerShell flow (`onepwr-aws-mesh/scripts/provisioning_registry.py`).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/api/provisioning/site-codes` | Canonical site dropdown (code/name/district/country). |
| POST | `/api/provisioning/things` | Validate → registry claim → create Thing (+type+attrs) → issue cert → attach `DevicePolicy` → record cert → return device **bootstrap** payload. |
| POST | `/api/provisioning/rotate` | Issue cert for the new Thing and publish `cfg/identity` to an online unit's *current* client id to rename it in place (migration). |
| GET  | `/api/provisioning/registry` | List the provisioning registry. |

All are role-gated to `superadmin` / `onm_team`.

The returned `bootstrap` object matches the firmware local-API schema
(`thing_name`, `ssid`, `password`, `version`, `cert_pem`, `key_pem`) and is POSTed
to the device at `http://<device-ip>/v1/provision/bootstrap`.

## Deploy prerequisite — IAM (applied)

The CC backend host (instance `i-04291e12e64de36d7`, af-south-1) runs under
instance profile `cc-postgres-backup-profile` → role **`cc-postgres-backup-role`**.
An inline policy **`cc-1meter-provisioning`** (applied 2026-06-22) grants, in
us-east-1:

- IoT control plane (`IoTProvisionControlPlane`): `iot:DescribeThing`,
  `iot:CreateThing`, `iot:UpdateThing`, `iot:DescribeThingType`,
  `iot:CreateThingType`, `iot:CreateKeysAndCertificate`,
  `iot:AttachThingPrincipal`, `iot:AttachPolicy`, `iot:ListThingPrincipals`,
  `iot:ListAttachedPolicies`.
- `iot:Publish` on `arn:…:topic/oneMeter/*` (`IoTPublishDeviceConfig`) — covers
  the rotate `cfg/identity` publish and relay_control's `cmd/relay`.
- DynamoDB on `1meter_provisioning_registry` (+ `index/*`) (`ProvisioningRegistry`):
  `GetItem`, `PutItem`, `UpdateItem`, `Query`, `Scan`.

Policy source of record: applied via `aws iam put-role-policy`. If the role is
ever recreated, re-apply the same three statements.

Env overrides (optional): `IOT_DEVICE_POLICY` (default `DevicePolicy`),
`IOT_THING_TYPE` (`OneMeter`), `IOT_ENDPOINT`, `PROVISIONING_REGISTRY_TABLE`,
`AWS_DEFAULT_REGION`.

## Multi-site

Works for any site registered in `country_config` (LS: MAK, MAS, SHG, …; BN: GBO,
SAM; etc.) with no code change — the site dropdown and the server-side validator
both read `ALL_SITE_ABBREV` / `ALL_KNOWN_SITES`. Add a new site's three-letter
code to `country_config.py` (per `docs/sop-add-new-country.md`) and it appears in
provisioning automatically.
