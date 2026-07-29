# 1Meter provisioning portal (CC)

GUI + API for taking a sealed 1Meter from factory boot firmware to a canonical
AWS IoT Thing and approved full firmware over signed OTA — without USB or AWS
credentials on the field laptop.

- **UI:** Portal → **Provisioning** (superadmin / O&M team). `src/pages/ProvisioningPage.tsx`.
- **API:** `acdb-api/meter_provisioning.py`, mounted at `/api/provisioning`.
- **SOP:** `onepwr-aws-mesh/Docs/SOP-1meter-operational-ota-provisioning.md` (primary path).
- **First-time field/depot SOP:** `acdb-api/provisioning_station_dist/START_HERE_FACTORY_VIRGIN_GATEWAYS.md`
  (included automatically in every station download). It is the operator-facing,
  no-USB factory-boot → Starlink → signed-OTA procedure.

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
| GET | `/api/provisioning/ota/readiness?site_code=GBO` | Fail-closed check that the selected site has an immutable approved artifact/version and that CC can access S3 + the signing profile. |
| POST | `/api/provisioning/ota/promote` | Create the signed AWS IoT OTA update. Candidate releases require one server-authorized test Thing and exact typed `CANARY <Thing>` confirmation; candidate-only releases cannot be batch-promoted. |
| GET | `/api/provisioning/ota/{update_id}` | Return creation status and per-Thing Job execution; persist OTA status and installed version on success. |
| GET | `/api/provisioning/meter-kit/download` | Download the pinned meter-addressing SOP/code plus the gateway/meter-string batch-validation SOP. |
| POST | `/api/provisioning/validation/sessions` | Start an isolated physical batch-validation session on an authorized test gateway. |
| GET | `/api/provisioning/validation/sessions/{id}` | Return fresh meter telemetry and the disconnect/reconnect command acknowledgements. |
| POST | `/api/provisioning/validation/sessions/{id}/observe` | Apply the physical load's energy increment to the synthetic test balance; zero queues relay-open. |
| POST | `/api/provisioning/validation/sessions/{id}/payment` | Add an isolated synthetic payment after acknowledged cutoff; queues relay-close. |
| POST | `/api/provisioning/validation/sessions/{id}/complete` | Pass only after positive load, acknowledged read-back `0`, and acknowledged read-back `1`. |
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

`factory-boot → identity-delivered → OTA-queued → full-fw-succeeded → online → meter-acquired → commissioned`

- **identity-delivered** = cert + identity + destination Starlink Wi-Fi written;
  Thing registered, but firmware promotion is not yet complete.
- **full-fw-succeeded** = the per-Thing AWS IoT Job is `SUCCEEDED`; CC records
  `ota_update_id`, target version, job status, and installed firmware version.
- **online** = reached AWS IoT at the install site.
- **meter-acquired** = telemetry seen; `reconcile` binds `thingName→meterId` so
  CC knows the gateway↔meter-serial pairing automatically.
- **commissioned** = meter serial ↔ customer account linked via the normal
  meter-assignment / commissioning workflow. The Thing name never changes.

`GET /provisioning/meters` returns an `allocation` field
(`unallocated|online|serial-acquired|allocated`) for segmenting these.

## Provisioning station (field tool)

A factory-boot gateway has no cert, so CC can't reach it directly and an HTTPS browser
page can't call its HTTP local API (mixed content). The **provisioning station**
(`onepwr-aws-mesh/tools/provisioning-station/`) is a small stdlib-only Python app
the technician runs on a laptop on the recognized `1Meter` LAN. It discovers the
gateway, asks for the exact destination-site Starlink SSID/password, allocates
names/certs through CC, and delivers the bootstrap locally. The gateway reboots
onto Starlink; CC then creates the signed full-firmware OTA and the station shows
per-Thing job progress.

Terminology is important:

- **Factory-virgin gateway:** sealed unit with factory boot firmware already
  installed. Field/depot staff do not open it and do not use USB.
- **Deployment Starlink:** each site has unique Wi-Fi credentials. The operator
  enters them in the station before identity delivery so the gateway can reboot
  onto Starlink, reach AWS IoT, and download the OTA.
- The actual destination Starlink router must be powered, online, and in range
  during this step. A unit at a depot without that destination network can hold
  a queued Job, but it is not fully provisioned until the Job succeeds.
- For optional HQ validation, the CC guide may branch to a controlled 2.4 GHz
  access point configured with the exact site SSID/password. The mirror must
  have internet, no captive portal/client isolation, and outbound MQTT 8883.
  Successful OTA on the mirror validates credential handoff and OTA only; the
  actual Starlink router and site RF/internet still require an installation
  check.
- **Provisioned gateway:** has a permanent `<SITE>-GW-####` identity. Do not
  erase, reflash, or provision it again for a network correction.

The banner ending in `Open: http://localhost:8787` means the local station is
running. Its terminal intentionally remains occupied while serving the UI.

The CC **Guide & download** tab is a gated six-step operator workflow: site
release check, two-network preparation, station startup, canary discovery,
bootstrap/OTA, and final verification. During OTA it polls the AWS status every
five seconds and shows an overall progress bar, queued/in-progress/succeeded/
failed counts, target version/update ID, and one status row per Thing. The
operator must confirm that CC’s auto-detected update ID matches the station
before the guide accepts completion.

The **OTA canary** tab is the engineering acceptance path for a new candidate.
CC displays only server-authorized test gateways, requires exact typed
confirmation, creates an OTA for one Thing, and shows live per-Thing progress.
A release configured with `canary_only: true` cannot be used for batch
promotion, even if its artifact/signing preflight passes.

The **Batch validation** tab is optional but recommended once per received or
assembled batch. Its downloadable kit includes:

- the pinned `set_meter_address.py` utility and full DDS8888 addressing SOP;
- the explicit reminder that USB-to-RS485 is used only on individual meters,
  never to provision a gateway;
- wiring/integration steps for combining uniquely addressed meters on the
  gateway RS-485 bus;
- a protected dummy-load test;
- an isolated synthetic balance/payment ledger that exercises the real
  CC → AWS IoT → gateway → meter relay → acknowledgement channel without
  creating customer revenue or production financial transactions.

The batch test cannot start unless the Thing is allowlisted, telemetry is
fresh, the Thing/meter binding matches, AWS reports OTA success, and gateway
telemetry reports the exact OTA target firmware. Completion requires a positive
physical energy delta, relay-open acknowledgement/read-back `0`, and
relay-close acknowledgement/read-back `1`.

All are role-gated to `superadmin` / `onm_team`.

The returned `bootstrap` object matches the firmware local-API schema
(`thing_name`, `ssid`, `password`, `version`, `cert_pem`, `key_pem`) and is POSTed
to the device at `http://<device-ip>/v1/provision/bootstrap`.

The Starlink password exists only in the request/bootstrap and device NVS. CC
stores the non-secret SSID and config version for operations, but never persists
or redisplays the password. Runtime NVS survives an application OTA; an optional
site-specific SSID baked into a release is fallback/build provenance, not the
primary credential path.

## CC awareness + locational tracking (1PDB)

Every provision/rotate is mirrored into 1PDB so CC is the **system of record** for
provisioned meters, not just a passthrough to DynamoDB:

- Table `meter_provisioning` (auto-created at startup): `thing_name` (unique),
  `meter_serial`, `pcb_mac`, `site`, `account_number`, `cert_id`/`cert_arn`,
  `status`, `legacy_id`, `deployment_wifi_ssid`, `wifi_config_version`,
  `ota_update_id`, `ota_target_version`, `ota_status`, `fw_version`, and
  timestamps. Written with the mutation audit. Wi-Fi passwords are never stored.
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
- OTA/Jobs + release verification: `iot:CreateOTAUpdate`, `iot:GetOTAUpdate`,
  `iot:ListJobExecutionsForJob`, S3 immutable object-version read, Signer
  profile/read/start permissions, and `iam:PassRole` restricted to
  `1pwr-ota-service-role`.

Policy source of record: applied via `aws iam put-role-policy`. If the role is
ever recreated, re-apply the same three statements.

Env overrides (optional): `IOT_DEVICE_POLICY` (default `DevicePolicy`),
`IOT_THING_TYPE` (`OneMeter`), `IOT_ENDPOINT`, `PROVISIONING_REGISTRY_TABLE`,
`AWS_DEFAULT_REGION`.

Release selection is fail-closed. Configure either the single-release variables
`ONEMETER_OTA_APP_KEY`, `ONEMETER_OTA_APP_VERSION_ID`, and
`ONEMETER_OTA_TARGET_VERSION`; `ONEMETER_FACTORY_BASELINE_VERSION` guards
anti-rollback (currently `1.1.56`). For site-specific release approval use
`ONEMETER_OTA_RELEASES_JSON` keyed by canonical
site. A per-site entry tracks the immutable artifact, version, optional
non-secret fallback SSID, signing profile/role overrides, and rollout rate.
When that environment variable is absent, CC reads the audited, non-secret
`acdb-api/ota_releases.json` catalog shipped with the deployment.
Set `canary_only: true` until the exact factory-v1.1.56 physical canary passes.
`ONEMETER_OTA_CANARY_THINGS` is a comma-separated server allowlist for the
single-device canary UI. `ONEMETER_VALIDATION_THINGS` separately allowlists
gateways that may operate a protected dummy load; if omitted it inherits the
canary allowlist. Never add an installed customer/field gateway to either list.

## Multi-site

Works for any site registered in `country_config` (LS: MAK, MAS, SHG, …; BN: GBO,
SAM; etc.) with no code change — the site dropdown and the server-side validator
both read `ALL_SITE_ABBREV` / `ALL_KNOWN_SITES`. Add a new site's three-letter
code to `country_config.py` (per `docs/sop-add-new-country.md`) and it appears in
provisioning automatically.
