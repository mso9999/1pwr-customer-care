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
| POST | `/api/provisioning/gateways` | **Batch** provision virgin gateways for a site, account-free. Allocates stable `<SITE>-GW-####` names (atomic per-site sequence) + cert each, returns bootstrap per unit. Used by the provisioning station. |
| POST | `/api/provisioning/rotate` | Issue cert for the new Thing and publish `cfg/identity` to an online unit's *current* client id to rename it in place (migration). |
| POST | `/api/provisioning/reconcile` | Bind provisioned gateways to acquired meter serials by reading DynamoDB `meter_last_seen` (`thingName`→`meterId`), filling `meter_serial` + online timestamps. Run periodically. |
| GET  | `/api/provisioning/meters` | CC system-of-record view: provisioned meters joined to `meters`/`accounts` for locational assignment (site, village, GPS, customer) + `allocation` stage. Optional `?site=MAK`. |
| GET  | `/api/provisioning/registry` | List the DynamoDB device/cert registry (bench + field). |

## Naming: two modes

- **Gateway pool (greenfield / batch):** `<SITE>-GW-####` — a stable, account-free
  device identity allocated by CC (atomic `gateway_pool_seq`). Use this when
  batch-provisioning units before they are installed/assigned. The customer
  account is **not** in the name; it is a later assignment tracked in 1PDB.
- **Site-account (known account / migration):** `<SITE>-<account>` (e.g.
  `MAK-0026`) — used when the account is already known (the original MAK
  migration). The Thing name is stable for life either way.

## Lifecycle (CC tracks every meter on this line)

`virgin → provisioned → online → meter-acquired → commissioned`

- **provisioned** = cert + identity + site Wi-Fi written; Thing registered; no
  account (the "provisioned but unallocated" bucket).
- **online** = reached AWS IoT at the install site.
- **meter-acquired** = telemetry seen; `reconcile` binds `thingName→meterId` so
  CC knows the gateway↔meter-serial pairing automatically.
- **commissioned** = meter serial ↔ customer account linked via the normal
  meter-assignment / commissioning workflow. The Thing name never changes.

`GET /provisioning/meters` returns an `allocation` field
(`unallocated|online|serial-acquired|allocated`) for segmenting these.

## Provisioning station (field tool)

A virgin gateway has no cert, so CC can't reach it directly and an HTTPS browser
page can't call its HTTP local API (mixed content). The **provisioning station**
(`onepwr-aws-mesh/tools/provisioning-station/`) is a small stdlib-only Python app
the technician runs on a laptop on the `1Meter` LAN: it scans/enumerates virgin
gateways, calls `/api/provisioning/gateways` to allocate names+certs, delivers
each bootstrap to the device's local API, and shows progress. Everything is
recorded in CC; the station holds no durable state.

All are role-gated to `superadmin` / `onm_team`.

The returned `bootstrap` object matches the firmware local-API schema
(`thing_name`, `ssid`, `password`, `version`, `cert_pem`, `key_pem`) and is POSTed
to the device at `http://<device-ip>/v1/provision/bootstrap`.

## CC awareness + locational tracking (1PDB)

Every provision/rotate is mirrored into 1PDB so CC is the **system of record** for
provisioned meters, not just a passthrough to DynamoDB:

- Table `meter_provisioning` (auto-created at startup): `thing_name` (unique),
  `meter_serial`, `pcb_mac`, `site`, `account_number`, `cert_id`/`cert_arn`,
  `status`, `legacy_id`, `provisioned_at`/`_by`. Written in the same transaction
  as the mutation-audit row.
- On provision it also best-effort tags the `meters` row (`platform='prototype'`,
  `community=<site>`) so the unit appears in the existing Meters views and
  inherits **village/GPS/customer** once assigned via the normal meter-assignment
  flow. Locational assignment therefore lives in CC's canonical `meters`/
  `accounts`/`meter_assignments` tables, keyed by account + community (site code).
- The portal **Provisioned meters** tab reads `/api/provisioning/meters`, showing
  each Thing with its serial, site, account, village, GPS, and status.

The DynamoDB registry stays the device/cert source of truth shared with the
firmware bench/HQ flow; 1PDB is CC's authoritative operational view.

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
