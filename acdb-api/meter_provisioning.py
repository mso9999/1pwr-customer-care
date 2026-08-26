"""
1Meter provisioning — CC-driven AWS IoT Thing + certificate issuance.

This is the cloud half of the GUI provisioning workflow described in
``onepwr-aws-mesh/Docs/SOP-1meter-operational-ota-provisioning.md``. It moves the
"create Thing + issue cert + claim registry + build bootstrap payload" logic out
of the field-laptop PowerShell kit (``Issue-Bootstrap-TLS.ps1``) and into the CC
backend, so an operator can provision a unit from the portal with no AWS CLI /
credentials on the laptop.

Why CC owns this
----------------
CC already holds the canonical **site codes** (``country_config.ALL_KNOWN_SITES``)
and customer **accounts**. Gateway Things are provisioned with stable
``<SITE>-GW-####`` names via the batch endpoint — customer accounts are linked
during commissioning, not provisioning. The provisioning registry (DynamoDB
``1meter_provisioning_registry``) stays the single source of truth for
PCB-MAC -> Thing, shared with the bench/HQ PowerShell path.

Flow
----
1. ``GET  /api/provisioning/site-codes``      -> canonical site dropdown.
2. ``POST /api/provisioning/gateways``        -> batch-provision virgin gateways
   with stable ``<SITE>-GW-####`` names (no customer account). Used by the
   provisioning station app.
3. ``GET  /api/provisioning/registry``        -> list registry rows.
4. ``POST /api/provisioning/update-config``   -> publish ``cfg/network`` to an
   already-provisioned gateway to update WiFi/SoftAP settings without changing
   the Thing name, certificates, or identity.
5. ``POST /api/provisioning/rotate``          -> publish ``cfg/identity`` to an
   already-online unit's *current* client id (superadmin-only, exceptional use
   for PCB reuse at another site).
6. ``GET  /api/provisioning/ota/readiness``   -> verify that CC has an approved,
   signed full-firmware artifact configured for factory-unit promotion.
7. ``POST /api/provisioning/ota/promote``     -> create one AWS IoT OTA update
   targeting the newly provisioned Things.
8. ``GET  /api/provisioning/ota/{id}``        -> report OTA creation and
   per-Thing job-execution status to the operator.

The bootstrap / cfg-identity payload schema is dictated by the firmware
(``local_api_server.c`` and ``device_control.c``): keys ``thing_name``, ``ssid``,
``password``, ``version``, ``cert_pem``, ``key_pem``.

Boto3 credentials: the CC host's IAM role/profile must allow the IoT control
plane (``iot:CreateThing``, ``CreateThingType``, ``DescribeThing*``,
``CreateKeysAndCertificate``, ``AttachThingPrincipal``, ``AttachPolicy``),
``iot-data:Publish`` (already used by relay_control), and DynamoDB read/write on
``1meter_provisioning_registry``. OTA promotion additionally requires
``iot:CreateOTAUpdate``, ``iot:GetOTAUpdate``, ``iot:ListJobExecutionsForJob``,
``s3:GetObjectVersion``, ``signer:StartSigningJob``, and ``iam:PassRole`` for
the OTA service role.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import hashlib
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field

from middleware import require_action, require_employee
from models import CCRole, CurrentUser
from mutations import try_log_mutation
from country_config import ALL_SITE_ABBREV, ALL_SITE_DISTRICTS, get_country_for_site

logger = logging.getLogger("cc-api.provisioning")

router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
REGISTRY_TABLE = os.environ.get("PROVISIONING_REGISTRY_TABLE", "1meter_provisioning_registry")
REGISTRY_THING_INDEX = os.environ.get("PROVISIONING_THING_INDEX", "thing_name-index")
THING_TYPE = os.environ.get("IOT_THING_TYPE", "OneMeter")
DEFAULT_POLICY = os.environ.get("IOT_DEVICE_POLICY", "DevicePolicy")
IOT_ENDPOINT = os.environ.get("IOT_ENDPOINT", "a3p95svnbmzyit-ats.iot.us-east-1.amazonaws.com")
IDENTITY_TOPIC_FMT = os.environ.get("IOT_IDENTITY_TOPIC_FMT", "oneMeter/{client_id}/cfg/identity")
NETWORK_TOPIC_FMT = os.environ.get("IOT_NETWORK_TOPIC_FMT", "oneMeter/{client_id}/cfg/network")

# Factory boot -> approved full-firmware OTA promotion. The non-secret AWS
# resource names have safe project defaults. The release-specific S3 key,
# immutable S3 VersionId, and target firmware version are intentionally
# fail-closed: an operator cannot schedule an OTA until deployment explicitly
# selects an approved artifact.
OTA_ACCOUNT_ID = os.environ.get("IOT_ACCOUNT_ID", "758201218523")
OTA_BUCKET = os.environ.get("ONEMETER_OTA_BUCKET", "1pwr-ota-firmware")
OTA_APP_KEY = os.environ.get("ONEMETER_OTA_APP_KEY", "")
OTA_APP_VERSION_ID = os.environ.get("ONEMETER_OTA_APP_VERSION_ID", "")
OTA_TARGET_VERSION = os.environ.get("ONEMETER_OTA_TARGET_VERSION", "")
OTA_FACTORY_BASELINE_VERSION = os.environ.get(
    "ONEMETER_FACTORY_BASELINE_VERSION", "1.1.56"
)
OTA_SIGNING_PROFILE = os.environ.get("ONEMETER_OTA_SIGNING_PROFILE", "1PWR_OTA_ESP32_v2")
OTA_ROLE_ARN = os.environ.get(
    "ONEMETER_OTA_ROLE_ARN",
    "arn:aws:iam::758201218523:role/1pwr-ota-service-role",
)
OTA_SIGNED_PREFIX = os.environ.get("ONEMETER_OTA_SIGNED_PREFIX", "signed/factory-promotion")
OTA_CERT_PATH = os.environ.get("ONEMETER_OTA_CERT_PATH_ON_DEVICE", "/")
OTA_MAX_PER_MINUTE = int(os.environ.get("ONEMETER_OTA_MAX_PER_MINUTE", "10"))
OTA_RELEASES_JSON = os.environ.get("ONEMETER_OTA_RELEASES_JSON", "")
OTA_RELEASE_CATALOG_PATH = os.environ.get(
    "ONEMETER_OTA_RELEASE_CATALOG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ota_releases.json"),
)
OTA_CANARY_THINGS = {
    item.strip()
    for item in os.environ.get("ONEMETER_OTA_CANARY_THINGS", "").split(",")
    if item.strip()
}

# Names we must never silently overwrite from the GUI. Bench/test identities
# belong to the HQ PowerShell flow; ad-hoc field names are exactly what this
# system exists to retire.
_BENCH_PREFIXES = ("HQTEST", "TEST-", "TESTSITE")

PROVISIONING_ROLES = (CCRole.superadmin, CCRole.onm_team, CCRole.engineering)
RELEASE_APPROVAL_ROLES = (CCRole.superadmin, CCRole.engineering)
CC_OPERATE_GATE = require_action(
    "operate_customer_care",
    system="cc",
    action="provision and commission gateways",
    required_level="C",
    fallback_roles=PROVISIONING_ROLES,
)
CC_APPROVE_GATE = require_action(
    "approve_financial_and_control",
    system="cc",
    action="approve and promote an OTA firmware release",
    required_level="B",
    fallback_roles=RELEASE_APPROVAL_ROLES,
)
CC_ADMIN_GATE = require_action(
    "administer_cc",
    system="cc",
    action="rotate a protected gateway identity",
    required_level="A",
    fallback_roles=(CCRole.superadmin,),
)
ACTIVATION_STEP_DEFS = {
    "deployment_wifi_ready": {
        "label": "Deployment Wi-Fi prepared",
        "owner": "Country O&M",
        "requires_evidence": False,
    },
    "meter_string_ready": {
        "label": "Meter string addressed and safely connected",
        "owner": "Country O&M",
        "requires_evidence": True,
    },
    "test_customer_assigned": {
        "label": "Test customer onboarded and meter assigned",
        "owner": "Country O&M",
        "requires_evidence": True,
    },
    "site_commissioning_verified": {
        "label": "Actual-site connectivity and commissioning verified",
        "owner": "Country O&M",
        "requires_evidence": True,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _active_site_map() -> dict[str, str]:
    """Country-local canonical sites for the active API lane.

    Tests and legacy one-process tools without COUNTRY_CODE retain the global
    map, while deployed /api, /api/bn and /api/zm services fail closed to their
    own country roster.  Both paths include the UI-managed ``country_sites``
    overlay so sites created from the admin UI are usable without a redeploy.
    """
    if not os.environ.get("COUNTRY_CODE"):
        from country_config import live_all_site_abbrev
        return live_all_site_abbrev()
    from country_config import COUNTRY, live_site_abbrev
    return live_site_abbrev(COUNTRY.code)


def _norm_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def _normalize_pem(pem: str) -> str:
    x = pem.replace("\r\n", "\n").replace("\r", "\n")
    if not x.endswith("\n"):
        x += "\n"
    return x


# ---------------------------------------------------------------------------
# boto3 clients (lazy so the module imports cleanly without AWS configured)
# ---------------------------------------------------------------------------


def _client(service: str):
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - deploy env always has boto3
        raise HTTPException(status_code=500, detail="boto3 not installed on the CC host") from exc
    return boto3.client(service, region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Name derivation + canonical validation
# ---------------------------------------------------------------------------


def derive_thing_name(site_code: str, account: str) -> str:
    """``MAK`` + ``0026MAK`` (or ``0026``) -> ``MAK-0026``.

    Enforces the canonical convention: the site code must be a real CC site, and
    if the account carries a trailing site suffix it must match.
    """
    site = (site_code or "").strip().upper()
    active_sites = _active_site_map()
    if site not in active_sites:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown site code '{site}'. It must be a canonical CC site "
                   f"code (one of: {', '.join(sorted(active_sites)) or 'none configured'}).",
        )

    acct = (account or "").strip().upper()
    if not acct:
        raise HTTPException(status_code=400, detail="account is required")

    # Account may be '0026MAK', '0026', or '26'. Pull the leading digit run.
    m = re.match(r"^(\d+)\s*([A-Z]{2,4})?$", acct)
    if not m:
        raise HTTPException(
            status_code=400,
            detail=f"account '{account}' is not in the expected '<digits>[SITE]' form.",
        )
    digits, suffix = m.group(1), m.group(2)
    if suffix and suffix != site:
        raise HTTPException(
            status_code=400,
            detail=f"account suffix '{suffix}' does not match site code '{site}'. "
                   f"Confirm the account belongs to this site.",
        )
    return f"{site}-{digits}"


def _validate_thing_name(thing: str):
    if not re.match(r"^[A-Za-z0-9_-]+$", thing):
        raise HTTPException(
            status_code=400,
            detail=f"Thing name '{thing}' has invalid characters (allowed: letters, digits, -, _).",
        )
    upper = thing.upper()
    for p in _BENCH_PREFIXES:
        if upper.startswith(p):
            raise HTTPException(
                status_code=400,
                detail=f"'{thing}' is a bench/test name. Production provisioning "
                       f"must use a canonical <SITE>-<account> name.",
            )


# ---------------------------------------------------------------------------
# DynamoDB registry helpers (mirror scripts/provisioning_registry.py schema)
# ---------------------------------------------------------------------------


def _registry_get_by_thing(thing: str) -> list[dict]:
    ddb = _client("dynamodb")
    try:
        resp = ddb.query(
            TableName=REGISTRY_TABLE,
            IndexName=REGISTRY_THING_INDEX,
            KeyConditionExpression="thing_name = :t",
            ExpressionAttributeValues={":t": {"S": thing}},
        )
        return resp.get("Items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("registry query by thing failed: %s", exc)
        return []


def _registry_get_by_mac(mac: str) -> Optional[dict]:
    ddb = _client("dynamodb")
    try:
        resp = ddb.get_item(TableName=REGISTRY_TABLE, Key={"pcb_mac": {"S": mac}})
        return resp.get("Item")
    except Exception as exc:  # noqa: BLE001
        logger.warning("registry get by mac failed: %s", exc)
        return None


def _registry_claim(
    mac: str,
    thing: str,
    *,
    site: str,
    operator: str,
    allow_rebind: bool = False,
    is_test: bool = False,
):
    """Atomic claim with the same guarantees as provisioning_registry.py claim.

    allow_rebind=True is used by the rename/rotate flow: the PCB is intentionally
    being moved from its current Thing to a new one, so the "PCB already bound to
    a different Thing" guard is relaxed (the binding is overwritten). The
    "target Thing already owned by a DIFFERENT PCB" guard is always enforced.
    """
    ddb = _client("dynamodb")

    for it in _registry_get_by_thing(thing):
        if it.get("pcb_mac", {}).get("S") != mac:
            raise HTTPException(
                status_code=409,
                detail=f"Thing '{thing}' is already claimed by PCB "
                       f"{it['pcb_mac']['S']} (not {mac}).",
            )

    existing = _registry_get_by_mac(mac)
    prior_thing = existing.get("thing_name", {}).get("S") if existing else None
    if existing and prior_thing != thing and not allow_rebind:
        raise HTTPException(
            status_code=409,
            detail=f"PCB {mac} is already bound to Thing '{prior_thing}', not "
                   f"'{thing}'. To change an already-provisioned unit's identity, "
                   f"use the Migrate / rename (rotate) flow.",
        )

    existing_is_test = bool((existing or {}).get("is_test", {}).get("BOOL"))
    item = {
        "pcb_mac": {"S": mac},
        "thing_name": {"S": thing},
        # A first-canary gateway is deliberately marked at allocation time.
        # This removes the field-team deadlock where an engineer previously had
        # to edit a server allow-list before CC would permit the first OTA.
        "is_test": {"BOOL": bool(is_test or (allow_rebind and existing_is_test))},
        "status": {"S": "claimed"},
        "claimed_at": {"S": _now()},
        "site": {"S": site},
        "operator": {"S": operator},
    }
    if existing and "provisioned_at" in existing:
        item["provisioned_at"] = existing["provisioned_at"]
    if prior_thing and prior_thing != thing:
        item["previous_thing_name"] = {"S": prior_thing}
    try:
        if allow_rebind:
            # Intentional rebind (rotate): overwrite the PCB->Thing mapping.
            ddb.put_item(TableName=REGISTRY_TABLE, Item=item)
        else:
            ddb.put_item(
                TableName=REGISTRY_TABLE,
                Item=item,
                ConditionExpression="attribute_not_exists(pcb_mac) OR thing_name = :t",
                ExpressionAttributeValues={":t": {"S": thing}},
            )
    except Exception as exc:  # noqa: BLE001 - includes ConditionalCheckFailed
        raise HTTPException(status_code=409, detail=f"registry claim failed: {exc}") from exc


def _registry_record_cert(mac: str, *, cert_arn: str, cert_id: str, meter_serial: str):
    ddb = _client("dynamodb")
    expr = "set cert_arn = :a, cert_id = :c, provisioned_at = :p, meter_serial = :m, #s = :s"
    vals = {
        ":a": {"S": cert_arn},
        ":c": {"S": cert_id},
        ":p": {"S": _now()},
        ":m": {"S": meter_serial},
        ":s": {"S": "provisioned"},
    }
    try:
        ddb.update_item(
            TableName=REGISTRY_TABLE,
            Key={"pcb_mac": {"S": mac}},
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(pcb_mac)",
            ExpressionAttributeValues=vals,
            ExpressionAttributeNames={"#s": "status"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("registry record-cert failed for %s: %s", mac, exc)


# ---------------------------------------------------------------------------
# 1PDB persistence — CC system of record for provisioned meters + location
# ---------------------------------------------------------------------------
#
# The DynamoDB registry remains the device/cert source of truth (shared with the
# firmware bench/HQ flow). We ALSO mirror every provisioning into 1PDB so CC is
# aware of provisioned meters and can track their locational assignment (site +
# account, joined to meters/accounts for village/GPS). 1PDB is CC's canonical
# datastore, so reporting, joins to customers/accounts, and audit all live here.


def ensure_meter_provisioning_table():
    """Create the meter_provisioning table if absent (idempotent, additive)."""
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            # Fail fast instead of hanging startup forever if a backup (pg_dump)
            # or long read holds a conflicting lock — these are all idempotent
            # (IF NOT EXISTS), so skipping on a locked table is safe; a later
            # startup re-applies them. (A missing lock_timeout here once took CC
            # down when a deploy overlapped the nightly pg_dump, 2026-07-06.)
            cur.execute("SET lock_timeout = '4s'")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meter_provisioning (
                    id              SERIAL PRIMARY KEY,
                    thing_name      VARCHAR(128) NOT NULL UNIQUE,
                    meter_serial    VARCHAR(64),
                    pcb_mac         VARCHAR(32),
                    site            VARCHAR(16),
                    account_number  VARCHAR(32),
                    cert_id         VARCHAR(128),
                    cert_arn        TEXT,
                    status          VARCHAR(24) NOT NULL DEFAULT 'provisioned',
                    legacy_id       VARCHAR(128),
                    fw_version      VARCHAR(32),
                    provisioned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    provisioned_by  TEXT,
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mp_serial ON meter_provisioning (meter_serial)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mp_site ON meter_provisioning (site)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mp_account ON meter_provisioning (account_number)")
            # Additive columns for gateway-pool batch provisioning + lifecycle tracking.
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS box_label VARCHAR(64)")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS first_seen_online TIMESTAMPTZ")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS last_seen_online TIMESTAMPTZ")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS ota_update_id VARCHAR(64)")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS ota_target_version VARCHAR(32)")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS ota_status VARCHAR(24)")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS ota_updated_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS deployment_wifi_ssid VARCHAR(64)")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS wifi_config_version INTEGER")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS wifi_configured_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS commissioned_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE meter_provisioning ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS onemeter_ota_release_approvals (
                    id                      BIGSERIAL PRIMARY KEY,
                    site_code               VARCHAR(16) NOT NULL,
                    artifact_version_id     TEXT NOT NULL,
                    target_firmware_version VARCHAR(32) NOT NULL,
                    canary_ota_update_id    VARCHAR(64) NOT NULL,
                    validation_session_id   UUID,
                    validation_waived       BOOLEAN NOT NULL DEFAULT FALSE,
                    waiver_reason           TEXT,
                    approved_by             TEXT NOT NULL,
                    approved_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    revoked_at              TIMESTAMPTZ,
                    revoked_by              TEXT,
                    UNIQUE (site_code, artifact_version_id, target_firmware_version)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS onemeter_activation_steps (
                    site_code       VARCHAR(16) NOT NULL,
                    step_key        VARCHAR(64) NOT NULL,
                    completed       BOOLEAN NOT NULL DEFAULT FALSE,
                    evidence_note   TEXT,
                    completed_by    TEXT,
                    completed_at    TIMESTAMPTZ,
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (site_code, step_key)
                )
            """)
            # Atomic per-site gateway sequence allocator (MAK-GW-0007 ...).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gateway_pool_seq (
                    site       VARCHAR(16) PRIMARY KEY,
                    last_seq   INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Field install binding: which provisioned gateway unit is in which
            # pole's PTB, captured at installation, with cloud-contact verification.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gateway_installation (
                    gateway_thing    VARCHAR(128) PRIMARY KEY,
                    site             VARCHAR(16) NOT NULL,
                    pole_id          VARCHAR(64) NOT NULL,
                    ptb_id           VARCHAR(128),
                    status           VARCHAR(24) NOT NULL DEFAULT 'awaiting_contact',
                    installed_by     TEXT,
                    installed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    first_online_at  TIMESTAMPTZ,
                    last_online_at   TIMESTAMPTZ,
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gi_site ON gateway_installation (site)")
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - never block app startup
        logger.error("meter_provisioning table init failed: %s", exc)


def _allocate_gateway_block(conn, site: str, count: int) -> list[int]:
    """Atomically reserve `count` gateway sequence numbers for a site.

    Returns the reserved sequence integers (ascending). First fills gaps
    from failed provisioning attempts (sequence numbers <= last_seq that
    have no corresponding meter_provisioning row), then advances the
    counter for any remaining slots. Uses a single UPSERT...RETURNING so
    concurrent provisioning stations can't collide on the counter.
    """
    cur = conn.cursor()

    # 1. Find existing GW sequence numbers for this site.
    cur.execute(
        """
        SELECT thing_name FROM meter_provisioning
        WHERE thing_name LIKE %s
        """,
        (f"{site}-GW-%%",),
    )
    used = set()
    for (thing,) in cur.fetchall():
        m = re.match(rf"^{re.escape(site)}-GW-(\d+)$", thing or "")
        if m:
            used.add(int(m.group(1)))

    # 2. Get current counter.
    cur.execute(
        "SELECT last_seq FROM gateway_pool_seq WHERE site = %s",
        (site,),
    )
    row = cur.fetchone()
    last_seq = int(row[0]) if row else 0

    # 3. Find gaps (unused numbers in [1, last_seq]).
    gaps = [n for n in range(1, last_seq + 1) if n not in used]

    # 4. Take gap numbers first, then allocate new numbers beyond last_seq.
    result = gaps[:count]
    remaining = count - len(result)
    if remaining > 0:
        cur.execute(
            """
            INSERT INTO gateway_pool_seq (site, last_seq, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (site) DO UPDATE
                SET last_seq = gateway_pool_seq.last_seq + EXCLUDED.last_seq,
                    updated_at = NOW()
            RETURNING last_seq
            """,
            (site, remaining),
        )
        new_max = int(cur.fetchone()[0])
        start = new_max - remaining + 1
        result.extend(range(start, new_max + 1))

    result.sort()
    return result


def _record_provisioning_1pdb(conn, *, thing, meter_serial, pcb_mac, site, account,
                              cert_id, cert_arn, status, fw_version, operator, legacy_id,
                              box_label=None, deployment_wifi_ssid=None,
                              wifi_config_version=None, is_test=False):
    """Upsert the CC-side provisioning record (caller owns the transaction).

    Also best-effort tags the meters row (platform/community/account) so the
    provisioned unit shows up with its site in the existing Meters views and can
    inherit village/GPS once it is assigned to a customer.
    """
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO meter_provisioning
            (thing_name, meter_serial, pcb_mac, site, account_number, cert_id,
             cert_arn, status, legacy_id, fw_version, provisioned_by, box_label,
             deployment_wifi_ssid, wifi_config_version, is_test,
             wifi_configured_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s IS NULL THEN NULL ELSE NOW() END, NOW())
        ON CONFLICT (thing_name) DO UPDATE SET
            meter_serial   = COALESCE(EXCLUDED.meter_serial, meter_provisioning.meter_serial),
            pcb_mac        = EXCLUDED.pcb_mac,
            site           = EXCLUDED.site,
            account_number = COALESCE(EXCLUDED.account_number, meter_provisioning.account_number),
            cert_id        = EXCLUDED.cert_id,
            cert_arn       = EXCLUDED.cert_arn,
            status         = EXCLUDED.status,
            legacy_id      = COALESCE(EXCLUDED.legacy_id, meter_provisioning.legacy_id),
            fw_version     = EXCLUDED.fw_version,
            provisioned_by = EXCLUDED.provisioned_by,
            box_label      = COALESCE(EXCLUDED.box_label, meter_provisioning.box_label),
            deployment_wifi_ssid = COALESCE(EXCLUDED.deployment_wifi_ssid, meter_provisioning.deployment_wifi_ssid),
            wifi_config_version = COALESCE(EXCLUDED.wifi_config_version, meter_provisioning.wifi_config_version),
            is_test        = EXCLUDED.is_test,
            wifi_configured_at = COALESCE(EXCLUDED.wifi_configured_at, meter_provisioning.wifi_configured_at),
            updated_at     = NOW()
        """,
        (thing, meter_serial, pcb_mac, site, account, cert_id, cert_arn,
         status, legacy_id, fw_version, operator, box_label, deployment_wifi_ssid,
         wifi_config_version, bool(is_test), deployment_wifi_ssid),
    )
    # Best-effort: ensure a meters row exists for this serial, tagged to the site.
    # Wrapped in a SAVEPOINT so a failure here (e.g. a NOT NULL column) cannot
    # poison the authoritative meter_provisioning write in the same transaction.
    # Does not overwrite an existing customer assignment's location fields.
    if meter_serial:
        cur.execute("SAVEPOINT mp_meter_tag")
        try:
            cur.execute("SELECT 1 FROM meters WHERE meter_id = %s", (meter_serial,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE meters SET platform = COALESCE(platform, 'prototype'), "
                    "community = COALESCE(NULLIF(community, ''), %s) WHERE meter_id = %s",
                    (site, meter_serial),
                )
            else:
                cur.execute(
                    "INSERT INTO meters (meter_id, community, account_number, platform, status) "
                    "VALUES (%s, %s, %s, 'prototype', 'active')",
                    (meter_serial, site, account),
                )
            cur.execute("RELEASE SAVEPOINT mp_meter_tag")
        except Exception as exc:  # noqa: BLE001 - meters tagging is best-effort
            cur.execute("ROLLBACK TO SAVEPOINT mp_meter_tag")
            cur.execute("RELEASE SAVEPOINT mp_meter_tag")
            logger.warning("meters tag on provision failed for %s: %s", meter_serial, exc)


# ---------------------------------------------------------------------------
# AWS IoT control-plane helpers
# ---------------------------------------------------------------------------


def _ensure_thing_type(iot):
    try:
        iot.describe_thing_type(thingTypeName=THING_TYPE)
    except iot.exceptions.ResourceNotFoundException:
        iot.create_thing_type(thingTypeName=THING_TYPE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("describe/create thing type failed: %s", exc)


def _ensure_thing(iot, thing: str, attrs: dict):
    try:
        iot.describe_thing(thingName=thing)
        if attrs:
            iot.update_thing(
                thingName=thing,
                thingTypeName=THING_TYPE,
                attributePayload={"attributes": attrs, "merge": True},
            )
    except iot.exceptions.ResourceNotFoundException:
        _ensure_thing_type(iot)
        iot.create_thing(
            thingName=thing,
            thingTypeName=THING_TYPE,
            attributePayload={"attributes": attrs},
        )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SiteCode(BaseModel):
    code: str
    name: str
    district: Optional[str] = None
    country: Optional[str] = None


class ProvisionRequest(BaseModel):
    site_code: str = Field(..., description="Canonical CC site code, e.g. MAK")
    account: str = Field(..., description="Customer account, e.g. 0026MAK or 0026")
    meter_serial: str = Field(..., min_length=3, max_length=32, description="Modbus serial, e.g. 23022613")
    pcb_mac: str = Field(..., description="Device PCB MAC (registry key)")
    wifi_ssid: str = Field(..., min_length=1, max_length=64)
    wifi_password: str = Field(..., min_length=1, max_length=128)
    policy_name: str = Field(default="", description="IoT policy to attach; defaults to DevicePolicy")
    version: int = Field(default=1, ge=1)
    legacy_id: Optional[str] = Field(default=None, description="Prior client id, recorded as an attribute")


class GatewayUnit(BaseModel):
    pcb_mac: str = Field(..., description="Device PCB MAC (durable registry key)")
    box_label: Optional[str] = Field(default=None, max_length=64,
                                     description="Physical asset/box label or QR (optional)")


class GatewayBatchRequest(BaseModel):
    """Batch-provision virgin gateways for a site, account-free.

    Each unit gets a stable gateway-pool Thing name <SITE>-GW-<seq>; the customer
    account is assigned later via the commissioning workflow.
    """
    site_code: str = Field(..., description="Destination site (canonical CC code)")
    units: list[GatewayUnit] = Field(..., min_length=1, max_length=200)
    wifi_ssid: str = Field(..., min_length=1, max_length=64)
    wifi_password: str = Field(..., min_length=1, max_length=128)
    softap_ssid: Optional[str] = Field(default=None, max_length=64,
                                        description="Optional SoftAP SSID for the device hotspot")
    softap_password: Optional[str] = Field(default=None, max_length=128,
                                            description="Optional SoftAP password for the device hotspot")
    policy_name: str = Field(default="")
    version: int = Field(default=1, ge=1)
    canary: bool = Field(
        default=False,
        description=(
            "Mark this single newly allocated gateway as the authorized first "
            "OTA/physical-validation test unit."
        ),
    )


class RotateRequest(BaseModel):
    current_client_id: str = Field(..., description="The unit's CURRENT MQTT client id (e.g. TestSite4)")
    site_code: str
    account: str
    meter_serial: str
    pcb_mac: str
    policy_name: str = Field(default="")
    version: int = Field(default=2, ge=1, description="Bump > current so the device accepts the new identity")


class UpdateConfigRequest(BaseModel):
    """WiFi/SoftAP configuration update for an already-provisioned gateway.

    Publishes to ``oneMeter/<thing>/cfg/network`` — does NOT touch the Thing
    name, certificates, or identity. The device applies the new WiFi config
    and reconnects. Use this for correcting mis-entered WiFi credentials or
    updating SoftAP settings after provisioning.
    """
    thing_name: str = Field(..., description="The gateway's permanent Thing name (e.g. MAK-GW-0001)")
    wifi_ssid: str = Field(..., min_length=1, max_length=64)
    wifi_password: str = Field(..., min_length=1, max_length=128)
    softap_ssid: Optional[str] = Field(default=None, max_length=64)
    softap_password: Optional[str] = Field(default=None, max_length=128)
    version: int = Field(default=1, ge=1, description="Monotonic version — must be higher than the device's current config version")


class OtaPromotionRequest(BaseModel):
    """Promote factory-boot gateways to CC's approved full firmware."""

    thing_names: list[str] = Field(..., min_length=1, max_length=200)
    site_code: str = Field(..., description="Canonical destination site; selects the approved release")
    note: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Optional operator note recorded in the mutation audit",
    )
    canary: bool = Field(
        default=False,
        description="Restrict this request to one server-authorized test gateway.",
    )
    confirmation: Optional[str] = Field(
        default=None,
        max_length=160,
        description="For a canary, must exactly equal 'CANARY <ThingName>'.",
    )


class OtaReleaseApprovalRequest(BaseModel):
    site_code: str
    canary_ota_update_id: str = Field(..., min_length=1, max_length=64)
    validation_session_id: Optional[str] = None
    waive_physical_validation: bool = False
    waiver_reason: Optional[str] = Field(default=None, max_length=500)
    confirmation: str = Field(..., min_length=1, max_length=160)


class ActivationStepUpdateRequest(BaseModel):
    site_code: str
    step_key: str
    completed: bool = True
    evidence_note: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


STATION_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisioning_station_dist")
STATION_BUNDLE_VERSION = "2026.08.09.1"
METER_KIT_FIRMWARE_COMMIT = "6ea321048c8fc23564e5d9de91fccc1d821162ae"
METER_KIT_FILES = {
    "METER_ADDRESSING.md": "10c8eecc99eeee35c1636c7446f4a053aba9d0a6b3f3263c6f2ee079ea6b2735",
    "set_meter_address.py": "1f37b7007006092f58547959999076b49b9a60b72d97a03aad3ab270685eff7f",
}


@router.get("/station/download")
def download_station(_user: CurrentUser = Depends(CC_OPERATE_GATE)):
    """Download the provisioning-station local app (zip) for the technician laptop.

    The station is a stdlib-only Python app the provisioner runs on the laptop;
    canonical source lives in onepwr-aws-mesh/tools/provisioning-station and is
    vendored here so CC can serve it.
    """
    if not os.path.isdir(STATION_DIST):
        raise HTTPException(status_code=404, detail="provisioning station bundle not found on server")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(STATION_DIST):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.join("provisioning-station", os.path.relpath(full, STATION_DIST))
                z.write(full, arc)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=provisioning-station-{STATION_BUNDLE_VERSION}.zip"
            ),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Provisioning-Station-Version": STATION_BUNDLE_VERSION,
        },
    )


@router.get("/meter-kit/download")
def download_meter_validation_kit(
    _user: CurrentUser = Depends(require_employee),
):
    """Download pinned meter-addressing code plus CC's batch-validation SOP.

    Any logged-in employee may download: this is a read-only field SOP + the
    Modbus addressing tool, not an operational control, so it uses the broad
    employee gate rather than the privileged provisioning action (so field staff
    like the Benin team can fetch it without a privileged role).
    """
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            local_sop = os.path.join(STATION_DIST, "METER_GATEWAY_BATCH_VALIDATION.md")
            z.write(local_sop, "1meter-validation/METER_GATEWAY_BATCH_VALIDATION.md")
            source_lines = [
                f"Firmware repository commit: {METER_KIT_FIRMWARE_COMMIT}",
                "Files are vendored in CC so the operator download does not depend on GitHub access.",
            ]
            for filename, expected_sha in METER_KIT_FILES.items():
                source_path = os.path.join(STATION_DIST, "meter-kit", filename)
                with open(source_path, "rb") as source_file:
                    content = source_file.read()
                actual_sha = hashlib.sha256(content).hexdigest()
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"{filename} checksum mismatch: expected {expected_sha}, got {actual_sha}"
                    )
                z.writestr(f"1meter-validation/{filename}", content)
                source_lines.append(f"{filename} SHA-256: {actual_sha}")
            z.writestr(
                "1meter-validation/SOURCE.txt",
                "\n".join(source_lines) + "\n",
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Unable to assemble the pinned meter-validation kit: {exc}",
        ) from exc
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=1meter-validation-kit.zip"},
    )


@router.get("/site-codes", response_model=list[SiteCode])
def list_site_codes(_user: CurrentUser = Depends(CC_OPERATE_GATE)):
    """Canonical site codes from CC's country config — the only valid prefixes."""
    from country_config import COUNTRY
    out = []
    for code, name in sorted(_active_site_map().items()):
        out.append(SiteCode(
            code=code,
            name=name,
            district=ALL_SITE_DISTRICTS.get(code),
            country=COUNTRY.code,
        ))
    return out


@router.get("/readiness")
def country_provisioning_readiness(
    _user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Country activation gates, ownership, and concrete next actions."""
    from country_config import COUNTRY

    sites = dict(_active_site_map())
    effective_tariff = COUNTRY.default_tariff_rate
    site_progress = {
        site: {
            "provisioned_gateways": 0,
            "production_gateways": 0,
            "test_gateways": 0,
            "ota_succeeded": 0,
            "passed_validations": 0,
            "commissioned": 0,
            "ota_candidate_ready": False,
            "ota_batch_approved": False,
            "operator_steps": {},
        }
        for site in sites
    }
    stats = {
        "provisioned_gateways": 0,
        "ota_succeeded": 0,
        "commissioned": 0,
        "test_gateways": 0,
        "passed_validations": 0,
    }
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM system_config WHERE key = 'tariff_rate' LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                effective_tariff = float(row[0])
            cur.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE UPPER(COALESCE(ota_status, '')) = 'SUCCEEDED'),
                       COUNT(*) FILTER (WHERE status = 'commissioned'),
                       COUNT(*) FILTER (WHERE is_test = TRUE)
                  FROM meter_provisioning
                """
            )
            counts = cur.fetchone() or (0, 0, 0, 0)
            stats.update(
                provisioned_gateways=int(counts[0]),
                ota_succeeded=int(counts[1]),
                commissioned=int(counts[2]),
                test_gateways=int(counts[3]),
            )
            cur.execute(
                """
                SELECT site,
                       COUNT(*),
                       COUNT(*) FILTER (WHERE is_test = FALSE),
                       COUNT(*) FILTER (WHERE is_test = TRUE),
                       COUNT(*) FILTER (WHERE UPPER(COALESCE(ota_status, '')) = 'SUCCEEDED'),
                       COUNT(*) FILTER (WHERE status = 'commissioned')
                  FROM meter_provisioning
                 WHERE site IS NOT NULL
                 GROUP BY site
                """
            )
            for site, provisioned, production, test, ota_ok, commissioned in cur.fetchall():
                if site in site_progress:
                    site_progress[site].update(
                        provisioned_gateways=int(provisioned),
                        production_gateways=int(production),
                        test_gateways=int(test),
                        ota_succeeded=int(ota_ok),
                        commissioned=int(commissioned),
                    )
            cur.execute(
                "SELECT COUNT(*) FROM onemeter_validation_sessions WHERE status = 'passed'"
            )
            stats["passed_validations"] = int((cur.fetchone() or [0])[0])
            cur.execute(
                """
                SELECT site_code, COUNT(*)
                  FROM onemeter_validation_sessions
                 WHERE status = 'passed'
                 GROUP BY site_code
                """
            )
            for site, passed in cur.fetchall():
                if site in site_progress:
                    site_progress[site]["passed_validations"] = int(passed)
            cur.execute(
                """
                SELECT site_code, step_key, completed, evidence_note,
                       completed_by, completed_at, updated_at
                  FROM onemeter_activation_steps
                """
            )
            for site, key, completed, note, operator, completed_at, updated_at in cur.fetchall():
                if site in site_progress and key in ACTIVATION_STEP_DEFS:
                    site_progress[site]["operator_steps"][key] = {
                        "completed": bool(completed),
                        "evidence_note": note,
                        "completed_by": operator,
                        "completed_at": str(completed_at) if completed_at else None,
                        "updated_at": str(updated_at) if updated_at else None,
                    }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to load provisioning readiness stats: %s", exc)

    ota_candidate_sites: list[str] = []
    ota_batch_sites: list[str] = []
    for site in sites:
        release = _ota_release(site)
        if not _ota_missing_config(release):
            ota_candidate_sites.append(site)
            site_progress[site]["ota_candidate_ready"] = True
            if not release.get("canary_only"):
                ota_batch_sites.append(site)
                site_progress[site]["ota_batch_approved"] = True

    payment_automation = os.environ.get(
        "PAYMENT_AUTOMATION_ENABLED",
        "0" if COUNTRY.code == "ZM" else "1",
    ).lower() in ("1", "true", "yes")
    meter_credit = os.environ.get(
        "METER_CREDIT_ENABLED",
        "0" if COUNTRY.code == "ZM" else "1",
    ).lower() in ("1", "true", "yes")

    gates = [
        {
            "key": "sites",
            "label": "Approved site roster",
            "ready": bool(sites),
            "scope": "provisioning",
            "owner": "Country lead + Engineering",
            "action": (
                "Use the approved canonical site codes shown below."
                if sites
                else (
                    "Country lead must approve the official name, unique site code, district/province, "
                    "deployment lead, and metering platform IDs. Engineering then adds the site to the "
                    "country configuration and deploys it. Open the site instructions for the exact sequence."
                )
            ),
            "route": "/help#sites",
        },
        {
            "key": "tariff",
            "label": "Commercial tariff",
            "ready": effective_tariff > 0,
            "scope": "payments",
            "owner": "Country lead + Finance/O&M",
            "action": (
                f"Configured at {effective_tariff:g} {COUNTRY.currency}/kWh."
                if effective_tariff > 0
                else "Approve the country tariff, then enter it on Tariffs."
            ),
            "route": "/tariffs",
        },
        {
            "key": "metering",
            "label": "Metering platform mapping",
            "ready": bool(COUNTRY.koios_org_id and COUNTRY.koios_sites),
            "scope": "commissioning",
            "owner": "Engineering",
            "action": (
                "Country metering organisation and sites are mapped."
                if COUNTRY.koios_org_id and COUNTRY.koios_sites
                else "Configure the approved metering organisation and per-site IDs."
            ),
            "route": None,
        },
        {
            "key": "payment_ingest",
            "label": "Automatic mobile-money ingestion",
            "ready": payment_automation,
            "scope": "payments",
            "owner": "Finance + Engineering",
            "action": (
                "Automatic payment ingestion is enabled."
                if payment_automation
                else "Provide real provider message samples and credentials; validate the parser before enabling ingestion."
            ),
            "route": "/payment-verification" if payment_automation else None,
        },
        {
            "key": "meter_credit",
            "label": "Meter credit and relay workflow",
            "ready": meter_credit,
            "scope": "commissioning",
            "owner": "O&M + Engineering",
            "action": (
                "Meter credit is enabled; prove relay behavior with Batch validation."
                if meter_credit
                else "Keep meter credit disabled until tariff, metering, and payment tests pass."
            ),
            "route": "/provisioning",
        },
        {
            "key": "ota_candidate",
            "label": "Site-specific OTA candidate",
            "ready": bool(sites) and len(ota_candidate_sites) == len(sites),
            "scope": "provisioning",
            "owner": "Firmware/Engineering",
            "action": (
                "Every configured site has an immutable OTA candidate."
                if sites and len(ota_candidate_sites) == len(sites)
                else "Publish and register an immutable signed OTA candidate for every deployment site."
            ),
            "route": "/provisioning",
        },
        {
            "key": "ota_canary",
            "label": "Successful OTA canary",
            "ready": stats["ota_succeeded"] > 0,
            "scope": "provisioning",
            "owner": "Country O&M",
            "action": (
                f"{stats['ota_succeeded']} gateway OTA result(s) are recorded as SUCCEEDED."
                if stats["ota_succeeded"] > 0
                else "Use Guide & download to allocate exactly one test gateway and run the first OTA canary."
            ),
            "route": "/provisioning",
        },
        {
            "key": "ota_batch",
            "label": "Batch release approval",
            "ready": bool(sites) and len(ota_batch_sites) == len(sites),
            "scope": "provisioning",
            "owner": "Engineering/Superadmin",
            "action": (
                "Every configured site candidate is approved for controlled batches."
                if sites and len(ota_batch_sites) == len(sites)
                else "After a successful canary, record physical validation or an explicit waiver and approve the immutable release in CC."
            ),
            "route": "/provisioning",
        },
    ]
    return {
        "country_code": COUNTRY.code,
        "country_name": COUNTRY.name,
        "currency": COUNTRY.currency,
        "sites": sites,
        "effective_tariff": effective_tariff,
        "ota_candidate_sites": ota_candidate_sites,
        "ota_batch_sites": ota_batch_sites,
        "site_progress": site_progress,
        "stats": stats,
        "gates": gates,
        "field_batch_ready": bool(gates) and all(
            gate["ready"] for gate in gates
            if gate["key"] in {"sites", "ota_candidate", "ota_canary", "ota_batch"}
        ),
        "end_to_end_ready": bool(gates) and all(gate["ready"] for gate in gates),
    }


@router.put("/activation-steps")
def update_activation_step(
    body: ActivationStepUpdateRequest,
    user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Record or clear an operator-confirmed physical activation step."""
    site = body.site_code.strip().upper()
    key = body.step_key.strip()
    definition = ACTIVATION_STEP_DEFS.get(key)
    if site not in _active_site_map():
        raise HTTPException(status_code=400, detail="Select a configured site in the active country.")
    if not definition:
        raise HTTPException(status_code=400, detail="Unknown activation checklist step.")
    note = (body.evidence_note or "").strip()
    if body.completed and definition["requires_evidence"] and len(note) < 5:
        raise HTTPException(
            status_code=400,
            detail="Add a short evidence reference before completing this physical step.",
        )

    from customer_api import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO onemeter_activation_steps
                (site_code, step_key, completed, evidence_note, completed_by,
                 completed_at, updated_at)
            VALUES (%s, %s, %s, %s, %s,
                    CASE WHEN %s THEN NOW() ELSE NULL END, NOW())
            ON CONFLICT (site_code, step_key) DO UPDATE SET
                completed = EXCLUDED.completed,
                evidence_note = EXCLUDED.evidence_note,
                completed_by = EXCLUDED.completed_by,
                completed_at = CASE WHEN EXCLUDED.completed THEN NOW() ELSE NULL END,
                updated_at = NOW()
            """,
            (
                site, key, body.completed, note or None, str(user.user_id),
                body.completed,
            ),
        )
        try_log_mutation(
            user,
            "update",
            "onemeter_activation_steps",
            f"{site}:{key}",
            new_values={
                "site_code": site,
                "step_key": key,
                "completed": body.completed,
                "evidence_note": note or None,
            },
            metadata={
                "kind": "onemeter_activation_walkthrough",
                "endpoint": "PUT /api/provisioning/activation-steps",
            },
            conn=conn,
        )
        conn.commit()
    return {
        "site_code": site,
        "step_key": key,
        "completed": body.completed,
        "evidence_note": note or None,
        "label": definition["label"],
        "owner": definition["owner"],
    }


def _approved_test_things(site_code: Optional[str] = None) -> set[str]:
    """Return environment-allowlisted and CC-allocated test gateways."""
    things = set(OTA_CANARY_THINGS)
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            if site_code:
                cur.execute(
                    """
                    SELECT thing_name
                      FROM meter_provisioning
                     WHERE is_test = TRUE AND site = %s
                    """,
                    (site_code.strip().upper(),),
                )
            else:
                cur.execute(
                    "SELECT thing_name FROM meter_provisioning WHERE is_test = TRUE"
                )
            things.update(str(row[0]) for row in cur.fetchall() if row and row[0])
    except Exception as exc:  # noqa: BLE001 - readiness remains useful pre-migration
        logger.debug("Unable to load CC test gateways: %s", exc)
    return things


def _release_approval(site: str, artifact_version_id: str, target_version: str) -> Optional[dict]:
    if not site or not artifact_version_id or not target_version:
        return None
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT canary_ota_update_id, validation_session_id,
                       validation_waived, waiver_reason, approved_by, approved_at
                  FROM onemeter_ota_release_approvals
                 WHERE site_code = %s
                   AND artifact_version_id = %s
                   AND target_firmware_version = %s
                   AND revoked_at IS NULL
                 LIMIT 1
                """,
                (site, artifact_version_id, target_version),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "canary_ota_update_id": row[0],
                "validation_session_id": str(row[1]) if row[1] else None,
                "validation_waived": bool(row[2]),
                "waiver_reason": row[3],
                "approved_by": row[4],
                "approved_at": str(row[5]) if row[5] else None,
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to load OTA release approval: %s", exc)
        return None


def _ota_release(site_code: Optional[str] = None) -> dict:
    """Resolve the immutable approved OTA release for a destination site.

    ``ONEMETER_OTA_RELEASES_JSON`` supports per-site release tracking without
    putting Wi-Fi passwords in CC configuration. Example value:
    ``{"GBO":{"artifact_key":"firmware/...bin","artifact_version_id":"...",
    "target_firmware_version":"1.1.57","fallback_ssid":"GBO-1Meter"}}``.
    Runtime SSID/password still come from the operator form and are stored in
    device NVS; ``fallback_ssid`` is non-secret build provenance only.
    """
    release = {
        "site_code": (site_code or "").strip().upper() or None,
        "region": AWS_REGION,
        "account_id": OTA_ACCOUNT_ID,
        "bucket": OTA_BUCKET,
        "artifact_key": OTA_APP_KEY,
        "artifact_version_id": OTA_APP_VERSION_ID,
        "target_firmware_version": OTA_TARGET_VERSION,
        "factory_baseline_version": OTA_FACTORY_BASELINE_VERSION,
        "signing_profile": OTA_SIGNING_PROFILE,
        "role_arn": OTA_ROLE_ARN,
        "signed_prefix": OTA_SIGNED_PREFIX,
        "certificate_path_on_device": OTA_CERT_PATH,
        "max_per_minute": OTA_MAX_PER_MINUTE,
        "credentials_mode": "runtime_nvs",
        "fallback_ssid": None,
        "canary_only": False,
        "catalog_canary_only": False,
        "approval": None,
        "approved_sites": [],
        "config_error": None,
    }
    catalog_json = OTA_RELEASES_JSON
    if not catalog_json and os.path.isfile(OTA_RELEASE_CATALOG_PATH):
        try:
            with open(OTA_RELEASE_CATALOG_PATH, encoding="utf-8") as catalog_file:
                catalog_json = catalog_file.read()
        except OSError as exc:
            release["config_error"] = f"Unable to read OTA release catalog: {exc}"
            return release
    if not catalog_json:
        return release
    try:
        catalog = json.loads(catalog_json)
        if not isinstance(catalog, dict):
            raise ValueError("root must be an object keyed by canonical site code")
    except Exception as exc:  # noqa: BLE001
        release["config_error"] = f"ONEMETER_OTA_RELEASES_JSON is invalid: {exc}"
        return release

    release["approved_sites"] = sorted(str(key).upper() for key in catalog)
    site = release["site_code"]
    if not site:
        return release
    site_release = catalog.get(site) or catalog.get(site.lower())
    if not isinstance(site_release, dict):
        # Fail closed rather than silently applying another site's artifact.
        release["artifact_key"] = ""
        release["artifact_version_id"] = ""
        release["target_firmware_version"] = ""
        return release
    for key in (
        "region", "account_id", "bucket", "artifact_key", "artifact_version_id",
        "target_firmware_version", "factory_baseline_version",
        "signing_profile", "role_arn",
        "signed_prefix", "certificate_path_on_device", "max_per_minute",
        "credentials_mode", "fallback_ssid",
        "canary_only",
    ):
        if key in site_release:
            release[key] = site_release[key]
    release["catalog_canary_only"] = bool(release.get("canary_only"))
    approval = _release_approval(
        site,
        str(release.get("artifact_version_id") or ""),
        str(release.get("target_firmware_version") or ""),
    )
    if approval:
        release["approval"] = approval
        release["canary_only"] = False
    return release


def _ota_missing_config(release: dict) -> list[str]:
    required = {
        "artifact_key": release.get("artifact_key"),
        "artifact_version_id": release.get("artifact_version_id"),
        "target_firmware_version": release.get("target_firmware_version"),
        "signing_profile": release.get("signing_profile"),
        "role_arn": release.get("role_arn"),
    }
    if release.get("config_error"):
        return [release["config_error"]]
    return [name for name, value in required.items() if not value]


def _ota_public_config(release: dict) -> dict:
    """Return non-secret release metadata suitable for the operator UI."""
    return {
        key: release.get(key)
        for key in (
            "site_code", "region", "bucket", "artifact_key",
            "artifact_version_id", "target_firmware_version", "signing_profile",
            "factory_baseline_version",
            "max_per_minute", "credentials_mode", "fallback_ssid",
            "approved_sites", "canary_only", "catalog_canary_only", "approval",
        )
    }


def _ota_update_id(things: list[str], target_version: str) -> str:
    version = re.sub(r"[^A-Za-z0-9_-]+", "-", target_version).strip("-")
    version = version[:18] or "full-fw"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha256(",".join(sorted(things)).encode("utf-8")).hexdigest()[:8]
    # AWS IoT OTA IDs (and their generated Jobs IDs) have a strict length cap.
    return f"1m-factory-{version}-{stamp}-{digest}"[:64]


def _firmware_version_tuple(value: object) -> tuple[int, int, int]:
    """Parse the OTA agent's MAJOR.MINOR.BUILD version format."""
    match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(value or ""))
    if not match:
        raise ValueError("expected MAJOR.MINOR.BUILD (for example 1.1.57)")
    return tuple(int(part) for part in match.groups())


def _ota_release_checks(release: dict) -> dict:
    """Run the non-mutating release checks used by both readiness and promote."""
    checks: dict[str, dict] = {}
    target = release.get("target_firmware_version")
    baseline = release.get("factory_baseline_version")
    try:
        target_tuple = _firmware_version_tuple(target)
        baseline_tuple = _firmware_version_tuple(baseline)
        if target_tuple <= baseline_tuple:
            raise ValueError(
                f"target {target} must be strictly newer than factory baseline {baseline}"
            )
        checks["anti_rollback"] = {
            "ok": True,
            "target_version": str(target),
            "factory_baseline_version": str(baseline),
        }
    except Exception as exc:  # noqa: BLE001
        checks["anti_rollback"] = {"ok": False, "error": str(exc)}

    try:
        head = _client("s3").head_object(
            Bucket=release["bucket"],
            Key=release["artifact_key"],
            VersionId=release["artifact_version_id"],
        )
        checks["artifact"] = {
            "ok": True,
            "bytes": head.get("ContentLength"),
            "etag": str(head.get("ETag") or "").strip('"') or None,
        }
    except Exception as exc:  # noqa: BLE001
        checks["artifact"] = {"ok": False, "error": str(exc)}

    try:
        profile = _client("signer").get_signing_profile(
            profileName=release["signing_profile"]
        )
        status = profile.get("status")
        if status != "Active":
            raise ValueError(f"signing profile status is {status or 'unknown'}, not Active")
        checks["signing_profile"] = {"ok": True, "status": status}
    except Exception as exc:  # noqa: BLE001
        checks["signing_profile"] = {"ok": False, "error": str(exc)}

    return checks


@router.get("/ota/readiness")
def ota_readiness(
    site_code: Optional[str] = None,
    _user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Fail-closed preflight for the factory-boot -> full-firmware OTA stage."""
    release = _ota_release(site_code)
    missing = _ota_missing_config(release)
    result = {
        "configured": not missing,
        "ready": False,
        "candidate_ready": False,
        "canary_ready": False,
        "canary_things": [],
        "site_required": bool(release.get("approved_sites")) and not release.get("site_code"),
        "missing": missing,
        "release": _ota_public_config(release),
        "checks": {},
    }
    if missing:
        return result

    result["checks"] = _ota_release_checks(release)
    checks_ok = all(check.get("ok") for check in result["checks"].values())
    test_things = _approved_test_things(release.get("site_code"))
    result["canary_things"] = sorted(test_things)
    # Candidate readiness deliberately does not require a pre-existing Thing:
    # the station can allocate exactly one new gateway and mark it as the
    # authorized test unit in the same audited operation.
    result["candidate_ready"] = checks_ok
    result["canary_ready"] = checks_ok and bool(test_things)
    result["ready"] = checks_ok and not bool(release.get("canary_only"))
    return result


@router.post("/ota/promote")
def promote_factory_gateways(
    payload: OtaPromotionRequest,
    user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Create a signed AWS IoT OTA update for newly provisioned factory units.

    The local station first gives each unit its durable Thing identity, TLS
    certificate, and operational Wi-Fi. This endpoint then queues the approved
    full application for those Things. The IoT Job remains queued while a unit
    reboots/joins the site LAN and starts as soon as it connects to AWS IoT.
    """
    site = payload.site_code.strip().upper()
    if site not in _active_site_map():
        raise HTTPException(status_code=400, detail=f"Unknown canonical site code '{site}'.")
    release = _ota_release(site)
    missing = _ota_missing_config(release)
    if missing:
        raise HTTPException(
            status_code=503,
            detail="Factory OTA promotion is not configured on CC. Missing: "
                   + ", ".join(missing),
        )
    checks = _ota_release_checks(release)
    failed_checks = [name for name, check in checks.items() if not check.get("ok")]
    if failed_checks:
        detail = "; ".join(
            f"{name}: {checks[name].get('error', 'failed')}" for name in failed_checks
        )
        raise HTTPException(
            status_code=503,
            detail=f"Factory OTA release failed preflight: {detail}",
        )

    things = list(dict.fromkeys(name.strip() for name in payload.thing_names if name.strip()))
    if not things:
        raise HTTPException(status_code=400, detail="At least one Thing name is required.")

    if payload.canary:
        if len(things) != 1:
            raise HTTPException(status_code=400, detail="A canary must target exactly one gateway.")
        expected_confirmation = f"CANARY {things[0]}"
        if payload.confirmation != expected_confirmation:
            raise HTTPException(
                status_code=400,
                detail=f"Type '{expected_confirmation}' exactly to start this canary.",
            )
    elif release.get("canary_only"):
        raise HTTPException(
            status_code=409,
            detail="This release is candidate-only. Complete and approve a single-gateway canary before batch rollout.",
        )

    for thing in things:
        if payload.canary:
            if not re.match(r"^[A-Za-z0-9_-]+$", thing):
                raise HTTPException(status_code=400, detail=f"Thing name '{thing}' has invalid characters.")
        else:
            _validate_thing_name(thing)
        rows = _registry_get_by_thing(thing)
        if not rows:
            raise HTTPException(
                status_code=409,
                detail=f"Thing '{thing}' is not in the provisioning registry. "
                       "Allocate its identity/certificate before scheduling OTA.",
            )
        marked_test = any(row.get("is_test", {}).get("BOOL") for row in rows)
        if payload.canary and thing not in OTA_CANARY_THINGS and not marked_test:
            raise HTTPException(
                status_code=403,
                detail=f"Thing '{thing}' is not authorized by the server as an OTA canary.",
            )
        if not payload.canary and marked_test:
            raise HTTPException(status_code=400, detail=f"Thing '{thing}' is marked as a test unit.")
        row_sites = {row.get("site", {}).get("S") for row in rows}
        if row_sites - {None, "", site}:
            raise HTTPException(
                status_code=409,
                detail=f"Thing '{thing}' is registered for {sorted(row_sites)}, not site {site}.",
            )

    target_version = str(release["target_firmware_version"])
    ota_id = _ota_update_id(things, target_version)
    target_arns = [
        f"arn:aws:iot:{release['region']}:{release['account_id']}:thing/{thing}"
        for thing in things
    ]
    signed_version = re.sub(r"[^A-Za-z0-9._-]+", "-", target_version).strip("-")
    signing: dict = {
        "startSigningJobParameter": {
            "signingProfileName": release["signing_profile"],
            "destination": {
                "s3Destination": {
                    "bucket": release["bucket"],
                    "prefix": f"{str(release['signed_prefix']).rstrip('/')}/{site}/{signed_version}",
                }
            },
        }
    }
    if release.get("certificate_path_on_device"):
        signing["startSigningJobParameter"]["signingProfileParameter"] = {
            "certificatePathOnDevice": release["certificate_path_on_device"]
        }

    file_entry = {
        "fileName": os.path.basename(release["artifact_key"]) or "FeaturedFreeRTOSIoTIntegration.bin",
        "fileType": 0,
        "fileVersion": target_version,
        "fileLocation": {
            "s3Location": {
                "bucket": release["bucket"],
                "key": release["artifact_key"],
                "version": release["artifact_version_id"],
            }
        },
        "codeSigning": signing,
        "attributes": {
            "workflow": "factory_boot_promotion",
            "target_version": target_version,
            "site": site,
            "credentials_mode": str(release.get("credentials_mode") or "runtime_nvs"),
        },
    }

    try:
        response = _client("iot").create_ota_update(
            otaUpdateId=ota_id,
            description=f"1Meter {site} factory boot promotion to {target_version}",
            targets=target_arns,
            protocols=["MQTT"],
            targetSelection="SNAPSHOT",
            files=[file_entry],
            roleArn=release["role_arn"],
            awsJobExecutionsRolloutConfig={"maximumPerMinute": int(release["max_per_minute"])},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"AWS rejected the OTA promotion request: {exc}",
        ) from exc

    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE meter_provisioning
                   SET ota_update_id = %s, ota_target_version = %s,
                       ota_status = 'QUEUED', ota_updated_at = NOW(),
                       updated_at = NOW()
                 WHERE thing_name = ANY(%s)
                """,
                (ota_id, target_version, things),
            )
            try_log_mutation(
                user, "update", "meter_provisioning", ota_id,
                new_values={
                    "ota_update_id": ota_id,
                    "target_version": target_version,
                    "site": site,
                    "thing_names": things,
                },
                metadata={
                    "kind": "factory_ota_canary" if payload.canary else "factory_ota_promotion",
                    "endpoint": "POST /api/provisioning/ota/promote",
                    "operator_note": payload.note,
                },
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTA promotion audit failed for %s: %s", ota_id, exc)

    return {
        "ota_update_id": response.get("otaUpdateId", ota_id),
        "aws_iot_job_id": response.get("awsIotJobId"),
        "status": response.get("otaUpdateStatus"),
        "target_version": target_version,
        "site_code": site,
        "credentials_mode": release.get("credentials_mode"),
        "thing_names": things,
        "canary": payload.canary,
        "status_url": f"/api/provisioning/ota/{ota_id}",
        "note": "OTA is queued. Leave the gateways powered and connected to the internet; "
                "completion must be confirmed per Thing before installation.",
    }


@router.post("/ota/release-approval")
def approve_ota_release(
    payload: OtaReleaseApprovalRequest,
    user: CurrentUser = Depends(CC_APPROVE_GATE),
):
    """Approve one immutable candidate for batch use after a successful canary.

    Physical meter/load/relay validation remains optional, but skipping it is
    an explicit, audited waiver rather than an invisible engineering action.
    """
    site = payload.site_code.strip().upper()
    if site not in _active_site_map():
        raise HTTPException(status_code=400, detail=f"Unknown canonical site code '{site}'.")
    release = _ota_release(site)
    missing = _ota_missing_config(release)
    if missing:
        raise HTTPException(
            status_code=503,
            detail="Candidate release is not configured: " + ", ".join(missing),
        )
    expected = f"APPROVE {site} {release['target_firmware_version']}"
    if payload.confirmation != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Type '{expected}' exactly to approve this immutable release.",
        )
    checks = _ota_release_checks(release)
    failed_checks = [name for name, check in checks.items() if not check.get("ok")]
    if failed_checks:
        raise HTTPException(
            status_code=503,
            detail="Release preflight failed: " + ", ".join(failed_checks),
        )

    from customer_api import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT thing_name, site, ota_status, ota_target_version, is_test
              FROM meter_provisioning
             WHERE ota_update_id = %s
             ORDER BY updated_at DESC
            """,
            (payload.canary_ota_update_id.strip(),),
        )
        rows = cur.fetchall()
        if len(rows) != 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Release approval requires exactly one recorded canary "
                    f"gateway; CC found {len(rows)} for this OTA update."
                ),
            )
        thing, row_site, ota_status, ota_target, is_test = rows[0]
        if str(row_site or "").upper() != site:
            raise HTTPException(status_code=409, detail="Canary site does not match this release.")
        if not is_test:
            raise HTTPException(status_code=409, detail="The canary gateway is not marked as a test unit.")
        if str(ota_status or "").upper() != "SUCCEEDED":
            raise HTTPException(
                status_code=409,
                detail="The canary OTA must be SUCCEEDED before release approval.",
            )
        if str(ota_target or "").lstrip("v") != str(release["target_firmware_version"]).lstrip("v"):
            raise HTTPException(
                status_code=409,
                detail="The canary target version does not match the candidate release.",
            )

        validation_id = payload.validation_session_id.strip() if payload.validation_session_id else None
        waiver_reason = (payload.waiver_reason or "").strip()
        if validation_id:
            cur.execute(
                """
                SELECT status, thing_name, site_code
                  FROM onemeter_validation_sessions
                 WHERE id = %s::uuid
                """,
                (validation_id,),
            )
            validation = cur.fetchone()
            if not validation:
                raise HTTPException(status_code=404, detail="Physical validation session not found.")
            if validation[0] != "passed" or validation[1] != thing or str(validation[2]).upper() != site:
                raise HTTPException(
                    status_code=409,
                    detail="Physical validation must be passed for this same canary gateway and site.",
                )
        elif not payload.waive_physical_validation:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Select a passed physical validation session, or explicitly "
                    "waive the optional validation with a reason."
                ),
            )
        elif len(waiver_reason) < 20:
            raise HTTPException(
                status_code=400,
                detail="A physical-validation waiver requires a reason of at least 20 characters.",
            )

        cur.execute(
            """
            INSERT INTO onemeter_ota_release_approvals
                (site_code, artifact_version_id, target_firmware_version,
                 canary_ota_update_id, validation_session_id,
                 validation_waived, waiver_reason, approved_by,
                 approved_at, revoked_at, revoked_by)
            VALUES (%s, %s, %s, %s, %s::uuid, %s, %s, %s, NOW(), NULL, NULL)
            ON CONFLICT (site_code, artifact_version_id, target_firmware_version)
            DO UPDATE SET
                canary_ota_update_id = EXCLUDED.canary_ota_update_id,
                validation_session_id = EXCLUDED.validation_session_id,
                validation_waived = EXCLUDED.validation_waived,
                waiver_reason = EXCLUDED.waiver_reason,
                approved_by = EXCLUDED.approved_by,
                approved_at = NOW(),
                revoked_at = NULL,
                revoked_by = NULL
            """,
            (
                site,
                str(release["artifact_version_id"]),
                str(release["target_firmware_version"]),
                payload.canary_ota_update_id.strip(),
                validation_id,
                bool(payload.waive_physical_validation),
                waiver_reason or None,
                str(user.user_id),
            ),
        )
        try_log_mutation(
            user,
            "update",
            "onemeter_ota_release_approvals",
            f"{site}:{release['target_firmware_version']}",
            new_values={
                "site": site,
                "artifact_version_id": release["artifact_version_id"],
                "target_firmware_version": release["target_firmware_version"],
                "canary_ota_update_id": payload.canary_ota_update_id,
                "validation_session_id": validation_id,
                "validation_waived": payload.waive_physical_validation,
            },
            metadata={
                "kind": "approve_ota_release",
                "endpoint": "POST /api/provisioning/ota/release-approval",
                "canary_thing": thing,
                "waiver_reason": waiver_reason or None,
            },
            conn=conn,
        )
        conn.commit()

    approved = _ota_release(site)
    return {
        "site_code": site,
        "target_firmware_version": approved["target_firmware_version"],
        "artifact_version_id": approved["artifact_version_id"],
        "ready": not approved["canary_only"],
        "approval": approved.get("approval"),
        "note": "The immutable release is approved for controlled batch provisioning.",
    }


@router.get("/ota/{ota_update_id}")
def ota_promotion_status(
    ota_update_id: str,
    _user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Return OTA creation state and per-Thing AWS IoT Job execution state."""
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", ota_update_id):
        raise HTTPException(status_code=400, detail="Invalid OTA update id.")
    try:
        info = _client("iot").get_ota_update(otaUpdateId=ota_update_id).get("otaUpdateInfo", {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to read OTA update: {exc}") from exc

    job_id = info.get("awsIotJobId")
    executions = []
    if job_id:
        try:
            resp = _client("iot").list_job_executions_for_job(jobId=job_id, maxResults=250)
            for item in resp.get("executionSummaries", []):
                summary = item.get("jobExecutionSummary", {})
                executions.append({
                    "thing_name": str(item.get("thingArn") or "").rsplit("/", 1)[-1],
                    "status": summary.get("status"),
                    "queued_at": str(summary.get("queuedAt") or "") or None,
                    "started_at": str(summary.get("startedAt") or "") or None,
                    "last_updated_at": str(summary.get("lastUpdatedAt") or "") or None,
                    "execution_number": summary.get("executionNumber"),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to list OTA job executions for %s: %s", job_id, exc)

    target_version = (info.get("files") or [{}])[0].get("fileVersion")
    if executions:
        try:
            from customer_api import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                for execution in executions:
                    status = execution.get("status")
                    thing = execution.get("thing_name")
                    cur.execute(
                        """
                        UPDATE meter_provisioning
                           SET ota_status = %s, ota_updated_at = NOW(),
                               fw_version = CASE WHEN %s = 'SUCCEEDED'
                                                 THEN COALESCE(%s, fw_version)
                                                 ELSE fw_version END,
                               updated_at = NOW()
                         WHERE thing_name = %s AND ota_update_id = %s
                        """,
                        (status, status, target_version, thing, ota_update_id),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to persist OTA status for %s: %s", ota_update_id, exc)

    return {
        "ota_update_id": ota_update_id,
        "ota_status": info.get("otaUpdateStatus"),
        "aws_iot_job_id": job_id,
        "target_version": target_version,
        "targets": info.get("targets", []),
        "executions": executions,
        "error_info": info.get("errorInfo"),
    }


def _issue_cert_and_payload(thing: str, attrs: dict, policy: str):
    """Create Thing + active cert, attach policy, return (cert_arn, cert_id, pem, key)."""
    iot = _client("iot")
    _ensure_thing(iot, thing, attrs)

    cert = iot.create_keys_and_certificate(setAsActive=True)
    cert_arn = cert["certificateArn"]
    cert_id = cert["certificateId"]
    cert_pem = _normalize_pem(cert["certificatePem"])
    key_pem = _normalize_pem(cert["keyPair"]["PrivateKey"])

    try:
        iot.attach_thing_principal(thingName=thing, principal=cert_arn)
        iot.attach_policy(policyName=policy, target=cert_arn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"cert attach failed: {exc}") from exc

    return cert_arn, cert_id, cert_pem, key_pem


@router.post("/things")
def provision_thing(
    payload: ProvisionRequest,
    user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Provision a new unit: canonical Thing + cert + registry claim + bootstrap payload.

    Returns the firmware ``bootstrap`` payload to POST to the device local API
    (``POST http://<device-ip>/v1/provision/bootstrap``).
    """
    thing = derive_thing_name(payload.site_code, payload.account)
    _validate_thing_name(thing)
    mac = _norm_mac(payload.pcb_mac)
    site = payload.site_code.strip().upper()
    policy = payload.policy_name.strip() or DEFAULT_POLICY

    _registry_claim(mac, thing, site=site, operator=f"cc:{user.user_id}")

    attrs = {
        "meter_serial": payload.meter_serial,
        "account": payload.account.strip(),
        "site": site,
    }
    if payload.legacy_id:
        attrs["legacy_id"] = payload.legacy_id

    cert_arn, cert_id, cert_pem, key_pem = _issue_cert_and_payload(thing, attrs, policy)
    _registry_record_cert(mac, cert_arn=cert_arn, cert_id=cert_id, meter_serial=payload.meter_serial)

    try:
        from customer_api import get_connection
        with get_connection() as conn:
            _record_provisioning_1pdb(
                conn, thing=thing, meter_serial=payload.meter_serial, pcb_mac=mac,
                site=site, account=payload.account.strip(), cert_id=cert_id,
                cert_arn=cert_arn, status="provisioned", fw_version=None,
                operator=f"cc:{user.user_id}", legacy_id=payload.legacy_id,
                deployment_wifi_ssid=payload.wifi_ssid,
                wifi_config_version=payload.version,
            )
            try_log_mutation(
                user, "create", "meter_provisioning", thing,
                new_values={"thing_name": thing, "meter_serial": payload.meter_serial,
                            "account": payload.account, "site": site, "cert_id": cert_id},
                metadata={"kind": "provision_thing", "endpoint": "POST /api/provisioning/things",
                          "pcb_mac": mac, "policy": policy},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - persistence/audit must never block provisioning
        logger.warning("provision 1PDB record/audit failed: %s", exc)

    bootstrap = {
        "thing_name": thing,
        "ssid": payload.wifi_ssid,
        "password": payload.wifi_password,
        "version": payload.version,
        "cert_pem": cert_pem,
        "key_pem": key_pem,
    }
    return {
        "thing_name": thing,
        "meter_serial": payload.meter_serial,
        "site": site,
        "account": payload.account.strip(),
        "certificate_arn": cert_arn,
        "certificate_id": cert_id,
        "policy": policy,
        "mqtt_endpoint": IOT_ENDPOINT,
        "bootstrap": bootstrap,
        "instructions": "POST the 'bootstrap' object to the device local API at "
                        "http://<device-ip>/v1/provision/bootstrap while connected to "
                        "the device SoftAP, then verify it reconnects as the new Thing.",
    }


@router.post("/gateways")
def provision_gateway_batch(
    payload: GatewayBatchRequest,
    user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Batch-provision virgin gateways for a site (account-free, gateway-pool names).

    Allocates a stable ``<SITE>-GW-<seq>`` Thing per unit, issues a cert, claims
    the registry by PCB MAC, records to 1PDB (status='provisioned', no account),
    and returns the device bootstrap payload per unit for the provisioning
    station to deliver on the local network.
    """
    site = payload.site_code.strip().upper()
    active_sites = _active_site_map()
    if site not in active_sites:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown site code '{site}'. Must be canonical "
                   f"(one of: {', '.join(sorted(active_sites)) or 'none configured'}).",
        )
    if payload.canary and len(payload.units) != 1:
        raise HTTPException(
            status_code=400,
            detail="A first-canary allocation must contain exactly one gateway.",
        )
    if payload.canary:
        existing_tests = _approved_test_things(site)
        if existing_tests:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This site already has an authorized test gateway: "
                    f"{', '.join(sorted(existing_tests))}. Use that recorded unit "
                    "or have Engineering retire it before allocating another."
                ),
            )
    policy = payload.policy_name.strip() or DEFAULT_POLICY

    # Reserve a contiguous gateway-number block for this site, atomically.
    from customer_api import get_connection
    with get_connection() as conn:
        seqs = _allocate_gateway_block(conn, site, len(payload.units))
        conn.commit()

    results = []
    errors = []
    for unit, seq in zip(payload.units, seqs):
        thing = f"{site}-GW-{seq:04d}"
        mac = _norm_mac(unit.pcb_mac)
        try:
            _validate_thing_name(thing)
            _registry_claim(
                mac,
                thing,
                site=site,
                operator=f"cc:{user.user_id}",
                is_test=payload.canary,
            )
            attrs = {
                "site": site,
                "role": "gateway",
                "legacy_id": "",
                "test_unit": "true" if payload.canary else "false",
            }
            cert_arn, cert_id, cert_pem, key_pem = _issue_cert_and_payload(thing, attrs, policy)
            _registry_record_cert(mac, cert_arn=cert_arn, cert_id=cert_id, meter_serial="")
            try:
                with get_connection() as conn:
                    _record_provisioning_1pdb(
                        conn, thing=thing, meter_serial=None, pcb_mac=mac, site=site,
                        account=None, cert_id=cert_id, cert_arn=cert_arn,
                        status="provisioned", fw_version=None,
                        operator=f"cc:{user.user_id}", legacy_id=None,
                        box_label=unit.box_label,
                        deployment_wifi_ssid=payload.wifi_ssid,
                        wifi_config_version=payload.version,
                        is_test=payload.canary,
                    )
                    try_log_mutation(
                        user, "create", "meter_provisioning", thing,
                        new_values={"thing_name": thing, "site": site, "pcb_mac": mac,
                                    "cert_id": cert_id, "box_label": unit.box_label,
                                    "is_test": payload.canary},
                        metadata={
                            "kind": "provision_gateway_canary" if payload.canary else "provision_gateway",
                            "endpoint": "POST /api/provisioning/gateways",
                        },
                        conn=conn,
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("gateway 1PDB record failed for %s: %s", thing, exc)

            bootstrap_payload = {
                "thing_name": thing,
                "ssid": payload.wifi_ssid,
                "password": payload.wifi_password,
                "version": payload.version,
                "cert_pem": cert_pem,
                "key_pem": key_pem,
            }
            if payload.softap_ssid:
                bootstrap_payload["softap_ssid"] = payload.softap_ssid
            if payload.softap_password:
                bootstrap_payload["softap_password"] = payload.softap_password

            results.append({
                "pcb_mac": mac,
                "thing_name": thing,
                "certificate_id": cert_id,
                "box_label": unit.box_label,
                "is_test": payload.canary,
                "bootstrap": bootstrap_payload,
            })
        except HTTPException as exc:
            errors.append({"pcb_mac": mac, "thing_name": thing, "error": exc.detail})
        except Exception as exc:  # noqa: BLE001
            errors.append({"pcb_mac": mac, "thing_name": thing, "error": str(exc)})

    return {
        "site": site,
        "requested": len(payload.units),
        "provisioned": len(results),
        "failed": len(errors),
        "mqtt_endpoint": IOT_ENDPOINT,
        "canary": payload.canary,
        "gateways": results,
        "errors": errors,
    }


@router.post("/reconcile")
def reconcile_from_telemetry(_user: CurrentUser = Depends(CC_OPERATE_GATE)):
    """Bind provisioned gateways to the meter serials they've acquired in the field.

    Reads DynamoDB ``meter_last_seen`` (which carries both ``meterId`` and
    ``thingName``) and fills ``meter_provisioning.meter_serial`` + online
    timestamps for matching Things. Safe to run repeatedly.
    """
    ddb = _client("dynamodb")
    seen = {}  # thing_name -> (meter_serial, last_ts)
    start = None
    try:
        while True:
            kwargs = {
                "TableName": os.environ.get("METER_LAST_SEEN_TABLE", "meter_last_seen"),
                "ProjectionExpression": "meterId, thingName, last_seen",
            }
            if start:
                kwargs["ExclusiveStartKey"] = start
            resp = ddb.scan(**kwargs)
            for it in resp.get("Items", []):
                thing = it.get("thingName", {}).get("S")
                serial = it.get("meterId", {}).get("S")
                ts = it.get("last_seen", {}).get("S") or it.get("last_seen", {}).get("N")
                if thing and serial:
                    seen[thing] = (serial, ts)
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"meter_last_seen scan failed: {exc}") from exc

    updated = 0
    conflicts: list[dict] = []
    from customer_api import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        for thing, (serial, _ts) in seen.items():
            cur.execute(
                "SELECT meter_serial, account_number, status "
                "FROM meter_provisioning WHERE thing_name = %s FOR UPDATE",
                (thing,),
            )
            current = cur.fetchone()
            if not current:
                continue
            current_serial = str(current[0] or "").strip()
            if current_serial and (current_serial.lstrip("0") or current_serial) != (
                str(serial).lstrip("0") or str(serial)
            ):
                conflicts.append({
                    "thing_name": thing,
                    "reported_meter_serial": serial,
                    "recorded_meter_serial": current_serial,
                    "reason": "gateway_reported_a_different_meter",
                })
                continue
            cur.execute(
                """
                SELECT thing_name
                  FROM meter_provisioning
                 WHERE meter_serial = %s
                   AND thing_name <> %s
                   AND status = 'commissioned'
                   AND NULLIF(account_number, '') IS NOT NULL
                 LIMIT 1
                """,
                (serial, thing),
            )
            owner = cur.fetchone()
            if owner:
                conflicts.append({
                    "thing_name": thing,
                    "reported_meter_serial": serial,
                    "recorded_meter_serial": current_serial or None,
                    "reason": f"meter_already_commissioned_through_{owner[0]}",
                })
                continue
            cur.execute(
                """
                UPDATE meter_provisioning
                SET meter_serial = COALESCE(meter_serial, %s),
                    first_seen_online = COALESCE(first_seen_online, NOW()),
                    last_seen_online = NOW(),
                    status = CASE WHEN status = 'provisioned' THEN 'online' ELSE status END,
                    updated_at = NOW()
                WHERE thing_name = %s
                """,
                (serial, thing),
            )
            updated += cur.rowcount
        conn.commit()
    return {
        "matched_things": len(seen),
        "rows_updated": updated,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


@router.post("/rotate")
def rotate_identity(
    payload: RotateRequest,
    user: CurrentUser = Depends(CC_ADMIN_GATE),
):
    """Rename an already-online unit by publishing ``cfg/identity`` to its CURRENT
    client id. Used to migrate ``TestSite*`` / ``OneMeterN`` units in place.

    The device must be online and running rotation-capable firmware. It reboots
    and reconnects under the new Thing name; watch ``oneMeter/<new>/...`` and the
    ``cfg/identity/ack`` topic to confirm.
    """
    new_thing = derive_thing_name(payload.site_code, payload.account)
    _validate_thing_name(new_thing)
    mac = _norm_mac(payload.pcb_mac)
    site = payload.site_code.strip().upper()
    policy = payload.policy_name.strip() or DEFAULT_POLICY

    # Rotation intentionally rebinds the PCB from its current Thing to the new one.
    _registry_claim(mac, new_thing, site=site, operator=f"cc:{user.user_id}", allow_rebind=True)

    attrs = {
        "meter_serial": payload.meter_serial,
        "account": payload.account.strip(),
        "site": site,
        "legacy_id": payload.current_client_id,
    }
    cert_arn, cert_id, cert_pem, key_pem = _issue_cert_and_payload(new_thing, attrs, policy)
    _registry_record_cert(mac, cert_arn=cert_arn, cert_id=cert_id, meter_serial=payload.meter_serial)

    identity_payload = {
        "thing_name": new_thing,
        "version": payload.version,
        "cert_pem": cert_pem,
        "key_pem": key_pem,
    }
    topic = IDENTITY_TOPIC_FMT.format(client_id=payload.current_client_id)

    try:
        iotdata = _client("iot-data")
        iotdata.publish(topic=topic, qos=1, payload=json.dumps(identity_payload).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"published cert but failed to publish cfg/identity to {topic}: {exc}",
        ) from exc

    try:
        from customer_api import get_connection
        with get_connection() as conn:
            _record_provisioning_1pdb(
                conn, thing=new_thing, meter_serial=payload.meter_serial, pcb_mac=mac,
                site=site, account=payload.account.strip(), cert_id=cert_id,
                cert_arn=cert_arn, status="rotating", fw_version=None,
                operator=f"cc:{user.user_id}", legacy_id=payload.current_client_id,
            )
            try_log_mutation(
                user, "update", "meter_provisioning", new_thing,
                new_values={"thing_name": new_thing, "from_client_id": payload.current_client_id,
                            "meter_serial": payload.meter_serial, "cert_id": cert_id},
                metadata={"kind": "rotate_identity", "endpoint": "POST /api/provisioning/rotate",
                          "topic": topic, "pcb_mac": mac},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rotate 1PDB record/audit failed: %s", exc)

    return {
        "new_thing_name": new_thing,
        "from_client_id": payload.current_client_id,
        "published_topic": topic,
        "certificate_id": cert_id,
        "ack_topic": f"oneMeter/{payload.current_client_id}/cfg/identity/ack",
        "note": "Device will reboot and reconnect under the new Thing. Confirm via the ack topic and fleet index.",
    }


@router.post("/update-config")
def update_device_config(
    payload: UpdateConfigRequest,
    user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """Update WiFi/SoftAP configuration on an already-provisioned gateway.

    Publishes a ``cfg/network`` payload to the device's Thing name via AWS IoT.
    The firmware applies the new WiFi settings with monotonic version checking
    and rollback-on-failure. This does NOT change the Thing name, certificates,
    or identity — it only updates operational network parameters.

    The device must be online and running firmware that supports the
    ``cfg/network`` topic. Watch ``oneMeter/<thing>/cfg/network/ack`` for
    confirmation.
    """
    thing = payload.thing_name.strip()
    _validate_thing_name(thing)

    network_payload: dict = {
        "ssid": payload.wifi_ssid,
        "password": payload.wifi_password,
        "version": payload.version,
    }
    if payload.softap_ssid:
        network_payload["softap_ssid"] = payload.softap_ssid
    if payload.softap_password:
        network_payload["softap_password"] = payload.softap_password

    topic = NETWORK_TOPIC_FMT.format(client_id=thing)

    try:
        iotdata = _client("iot-data")
        iotdata.publish(topic=topic, qos=1, payload=json.dumps(network_payload).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Failed to publish cfg/network to {topic}: {exc}",
        ) from exc

    try:
        from customer_api import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE meter_provisioning
                   SET deployment_wifi_ssid = %s,
                       wifi_config_version = %s,
                       wifi_configured_at = NOW(),
                       updated_at = NOW()
                 WHERE thing_name = %s
                """,
                (payload.wifi_ssid, payload.version, thing),
            )
            try_log_mutation(
                user, "update", "meter_provisioning", thing,
                new_values={"wifi_ssid": payload.wifi_ssid, "version": payload.version},
                metadata={"kind": "update_config", "endpoint": "POST /api/provisioning/update-config",
                          "topic": topic, "thing_name": thing},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("update-config audit failed for %s: %s", thing, exc)

    return {
        "thing_name": thing,
        "published_topic": topic,
        "ack_topic": f"oneMeter/{thing}/cfg/network/ack",
        "version": payload.version,
        "note": "Device will apply the new WiFi settings and reconnect. Confirm via the ack topic.",
    }


@router.get("/meters")
def list_provisioned_meters(
    site: Optional[str] = None,
    _user: CurrentUser = Depends(CC_OPERATE_GATE),
):
    """CC's system-of-record view of provisioned meters + locational assignment.

    Joins the 1PDB ``meter_provisioning`` records to ``meters`` (via meter serial)
    and ``accounts`` (via account number) so each row carries both the IoT
    identity and where the unit is assigned (site/community, village, GPS,
    customer account).
    """
    from customer_api import get_connection
    sql = """
        SELECT mp.thing_name, mp.meter_serial, mp.pcb_mac, mp.site,
               mp.account_number, mp.status, mp.cert_id, mp.legacy_id,
               mp.box_label, mp.first_seen_online, mp.last_seen_online,
               mp.deployment_wifi_ssid, mp.wifi_config_version,
               mp.fw_version, mp.ota_target_version, mp.ota_update_id,
               mp.ota_status, mp.ota_updated_at,
               mp.is_test,
               mp.provisioned_at, mp.provisioned_by, mp.updated_at,
               m.community AS meter_community, m.village_name, m.latitude,
               m.longitude, m.status AS meter_status, m.customer_type,
               a.customer_id
        FROM meter_provisioning mp
        LEFT JOIN meters m ON m.meter_id = mp.meter_serial
        LEFT JOIN accounts a ON a.account_number = mp.account_number
        {where}
        ORDER BY mp.provisioned_at DESC
    """
    params: list = []
    where = ""
    if site:
        where = "WHERE mp.site = %s"
        params.append(site.strip().upper())
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql.format(where=where), params)
        except Exception as exc:  # noqa: BLE001 - table may not exist yet
            logger.warning("list provisioned meters failed: %s", exc)
            return {"count": 0, "meters": []}
        cols = [d[0] for d in cur.description]
        rows = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k, v in d.items():
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    d[k] = str(v)
            # Lifecycle segment for the UI.
            if d.get("account_number") or d.get("customer_id"):
                d["allocation"] = "allocated"
            elif d.get("meter_serial"):
                d["allocation"] = "serial-acquired"
            elif d.get("last_seen_online"):
                d["allocation"] = "online"
            else:
                d["allocation"] = "unallocated"
            rows.append(d)
    return {"count": len(rows), "meters": rows}


@router.get("/fleet-map")
def fleet_map(
    site: Optional[str] = None,
    _user: CurrentUser = Depends(require_employee),
):
    """Fleet map data: meters with GPS + online/reporting status.

    Joins 1PDB `meters` (location, account, status) with DynamoDB
    `meter_last_seen` (telemetry recency + Thing). Status per meter:
      - online:    reported telemetry within the last 24h
      - offline:   installed (account + GPS) but no recent telemetry
      - no-gps:    no real coordinates (shown in count only, not on the map)
    Employee-readable (map is a read-only view).
    """
    from datetime import datetime, timezone, timedelta
    from customer_api import get_connection

    site_code = site.strip().upper() if site else None

    # 1PDB meters with location, flagged when the meter is a linked 1Meter.
    # A meter is linked when its serial is in meter_provisioning (primary meter
    # of a provisioned gateway) OR in meter_gateway_link (assigned to a pole/PTB
    # at assign-PTB time — the gateway leg may still be pending telemetry).
    # The JOINs fan out (a meter can match several provisioning rows and a link
    # row), so aggregate back to one row per meter: MAX() ignores NULLs and
    # picks the known gateway/pole when present.
    with get_connection() as conn:
        cur = conn.cursor()
        sql = """
            SELECT m.meter_id, m.account_number, m.community, m.village_name,
                   m.latitude, m.longitude, m.status, m.platform,
                   BOOL_OR(p.thing_name IS NOT NULL OR gl.meter_serial IS NOT NULL) AS linked,
                   MAX(p.thing_name) AS prov_thing,
                   MAX(gl.gateway_thing) AS link_thing,
                   MAX(gl.pole_id) AS link_pole
            FROM meters m
            LEFT JOIN meter_provisioning p
              ON p.meter_serial = m.meter_id
              OR p.meter_serial = m.meter_number
              OR ltrim(p.meter_serial, '0') = ltrim(m.meter_id, '0')
            LEFT JOIN meter_gateway_link gl
              ON ltrim(gl.meter_serial, '0') = ltrim(m.meter_id, '0')
              OR ltrim(gl.meter_serial, '0') = ltrim(m.meter_number, '0')
            WHERE m.latitude IS NOT NULL AND m.longitude IS NOT NULL
        """
        params: list = []
        if site_code:
            sql += " AND m.community = %s"
            params.append(site_code)
        sql += """
            GROUP BY m.meter_id, m.account_number, m.community, m.village_name,
                     m.latitude, m.longitude, m.status, m.platform
        """
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        meters = [dict(zip(cols, r)) for r in cur.fetchall()]

    # DynamoDB meter_last_seen: meterId -> thingName + last_seen
    seen = {}
    try:
        ddb = _client("dynamodb")
        start = None
        while True:
            kw = {"TableName": "meter_last_seen",
                  "ProjectionExpression": "meterId, thingName, last_seen, lastAcceptedTime"}
            if start:
                kw["ExclusiveStartKey"] = start
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                def g(k):
                    v = it.get(k, {})
                    return list(v.values())[0] if v else None
                mid = g("meterId")
                if mid:
                    # Fall back to lastAcceptedTime (the meter's last data sample) when
                    # last_seen (ingestion timestamp) is absent — e.g. units that last
                    # reported before the last_seen field was added to the pipeline.
                    seen[mid] = {"thing_name": g("thingName"),
                                 "last_seen": g("last_seen") or g("lastAcceptedTime")}
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
    except Exception as exc:
        logger.warning("fleet-map meter_last_seen scan failed: %s", exc)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    def parse_seen(ts):
        if not ts:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M"):
            try:
                dt = datetime.strptime(str(ts), fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    out = []
    no_gps = 0
    for m in meters:
        mid = str(m.get("meter_id") or "")
        # skip the shared default/center coordinate (not a real install location)
        try:
            lat = float(m["latitude"]); lng = float(m["longitude"])
        except (TypeError, ValueError):
            no_gps += 1
            continue
        sn = mid.lstrip("0") or mid
        srec = seen.get(mid) or seen.get(sn) or seen.get(mid.zfill(12))
        last_seen = srec.get("last_seen") if srec else None
        thing = srec.get("thing_name") if srec else None
        seen_dt = parse_seen(last_seen)
        online = bool(seen_dt and seen_dt >= cutoff)
        resolved_thing = thing or m.get("prov_thing") or m.get("link_thing")
        out.append({
            "meter_id": mid,
            "account_number": m.get("account_number"),
            "village": m.get("village_name"),
            "lat": lat,
            "lng": lng,
            "status": m.get("status"),
            "platform": m.get("platform"),
            "thing_name": resolved_thing,
            "linked": bool(m.get("linked")),
            "pole_id": m.get("link_pole"),
            # Linked via pole/PTB but the gateway hasn't been identified yet
            # (no telemetry, no provisioning record) — show it explicitly
            # instead of a bare popup that looks unlinked.
            "gateway_pending": bool(m.get("linked")) and not resolved_thing,
            "last_seen": last_seen,
            "online": online,
        })

    online_n = sum(1 for r in out if r["online"])
    return {
        "site": site_code,
        "total": len(out),
        "online": online_n,
        "offline": len(out) - online_n,
        "no_gps": no_gps,
        "meters": out,
    }


@router.get("/customer-linkage/{account_number}")
def customer_meter_linkage(
    account_number: str,
    _user: CurrentUser = Depends(require_employee),
):
    """Meter & linkage card for a customer detail view.

    For the given account, returns the linked 1Meter gateway (Thing / PCB), the
    customer meter serial it reads, last-comms recency (from DynamoDB
    `meter_last_seen`), and the physical pole/PTB (best-effort, from uGridPLAN
    via the customer's connection). Read-only; employee-readable.
    """
    from datetime import datetime, timezone, timedelta
    from customer_api import get_connection

    acct = account_number.strip()
    acct_upper = acct.upper()
    # Normalised account without the site suffix (e.g. '4321MAK' -> '4321') so we
    # match meter_provisioning rows whether they stored '4321MAK' or '4321'.
    import re as _re
    m = _re.match(r"^(\d{3,5})([A-Za-z]{2,4})?$", acct_upper)
    bare = m.group(1) if m else None
    site_suffix = m.group(2) if m else None

    # 1) meter_provisioning row for this account (gateway Thing / PCB / serial).
    #    Batch-provisioned gateways (MAK-GW-*) have account_number NULL — the link
    #    to a customer is via the customer's METER serial (meters.account_number ->
    #    meters.meter_id == meter_provisioning.meter_serial), so we fall back to a
    #    serial join when the account match finds nothing.
    prov = None
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT thing_name, meter_serial, pcb_mac, site, account_number, status,
                   box_label, commissioned_at, fw_version
            FROM meter_provisioning
            WHERE UPPER(account_number) = %s OR UPPER(account_number) = %s
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
            """,
            (acct_upper, bare or acct_upper),
        )
        row = cur.fetchone()
        if not row:
            # Serial join via the customer's meter (zero-normalised on both sides).
            cur.execute(
                """
                SELECT p.thing_name, p.meter_serial, p.pcb_mac, p.site, p.account_number,
                       p.status, p.box_label, p.commissioned_at, p.fw_version
                FROM meter_provisioning p
                WHERE p.meter_serial IS NOT NULL AND p.meter_serial <> ''
                  AND EXISTS (
                      SELECT 1 FROM meters m
                      WHERE UPPER(m.account_number) = %s
                        AND (ltrim(m.meter_id, '0') = ltrim(p.meter_serial, '0')
                             OR ltrim(m.meter_number, '0') = ltrim(p.meter_serial, '0'))
                  )
                ORDER BY p.updated_at DESC NULLS LAST
                LIMIT 1
                """,
                (acct_upper,),
            )
            row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            prov = dict(zip(cols, row))
        else:
            # PTB-channel link (assign-PTB): meter_gateway_link keyed by account.
            cur.execute(
                """
                SELECT gateway_thing, meter_serial, site, account_number, pole_id, ptb_id
                FROM meter_gateway_link
                WHERE UPPER(account_number) = %s OR UPPER(account_number) = %s
                ORDER BY linked_at DESC NULLS LAST
                LIMIT 1
                """,
                (acct_upper, bare or acct_upper),
            )
            gl = cur.fetchone()
            if gl:
                prov = {"thing_name": gl[0], "meter_serial": gl[1], "pcb_mac": None,
                        "site": gl[2], "account_number": gl[3], "status": "linked",
                        "box_label": None, "commissioned_at": None, "fw_version": None,
                        "pole_id": gl[4]}

        # 2) customer plot_number + community (for the pole lookup)
        cust = None
        try:
            cur.execute(
                "SELECT plot_number, community FROM customers WHERE UPPER(plot_number) LIKE %s LIMIT 1",
                (f"%{bare}%" if bare else f"%{acct_upper}%",),
            )
            crow = cur.fetchone()
            if crow:
                cust = {"plot_number": crow[0], "community": crow[1]}
        except Exception:
            cust = None

    if not prov:
        return {"account_number": acct, "linked": False}

    meter_serial = (prov.get("meter_serial") or "").strip()
    thing_name = prov.get("thing_name")
    site = (prov.get("site") or site_suffix or (cust or {}).get("community") or "").strip()

    # 3) last comms from DynamoDB meter_last_seen (keyed by meter serial)
    last_seen = None
    reporting_thing = None
    online = False
    if meter_serial:
        try:
            ddb = _client("dynamodb")
            variants = {meter_serial, meter_serial.lstrip("0") or meter_serial, meter_serial.zfill(12)}
            for mv in variants:
                resp = ddb.get_item(
                    TableName="meter_last_seen",
                    Key={"meterId": {"S": mv}},
                    ProjectionExpression="meterId, thingName, last_seen, lastAcceptedTime",
                )
                it = resp.get("Item")
                if it:
                    # Fall back to lastAcceptedTime when last_seen is absent (units
                    # that last reported before the last_seen field existed).
                    last_seen = it.get("last_seen", {}).get("S") or it.get("lastAcceptedTime", {}).get("S")
                    reporting_thing = it.get("thingName", {}).get("S")
                    break
            if last_seen:
                dt = None
                for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M"):
                    try:
                        dt = datetime.strptime(str(last_seen), fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                online = bool(dt and dt >= (datetime.now(timezone.utc) - timedelta(hours=24)))
        except Exception as exc:
            logger.warning("customer-linkage meter_last_seen lookup failed: %s", exc)

    # 4) pole/PTB: prefer the recorded assign-PTB link (meter_gateway_link);
    #    else best-effort from uGridPLAN via the customer's connection.
    pole_id = prov.get("pole_id")
    if not pole_id and cust and cust.get("plot_number") and site:
        try:
            import sync_ugridplan as sug
            with sug.get_auth_db() as aconn:
                prow = aconn.execute(
                    "SELECT project_id FROM cc_site_projects WHERE site_code = ?",
                    (site.upper(),),
                ).fetchone()
            if prow:
                client = sug._get_ugp_client()
                session_id = sug._load_project_for_site(client, prow["project_id"])
                lines = client.get_lines(session_id)
                # plot_number is the connection Survey_ID in the common case
                pole_id = sug._pole_for_connection(lines, str(cust["plot_number"]).strip())
        except Exception as exc:
            logger.info("customer-linkage pole lookup failed (best-effort): %s", exc)

    return {
        "account_number": acct,
        "linked": True,
        "meter_serial": meter_serial or None,
        "thing_name": thing_name,
        "reporting_thing": reporting_thing,
        "pcb_mac": prov.get("pcb_mac"),
        "box_label": prov.get("box_label"),
        "site": site or None,
        "status": prov.get("status"),
        "fw_version": prov.get("fw_version"),
        "commissioned_at": str(prov.get("commissioned_at")) if prov.get("commissioned_at") else None,
        "last_seen": last_seen,
        "online": online,
        "pole_id": pole_id,
    }


# --- Identity reconciliation & harmonization --------------------------------
#
# Canonical identity = the meter_provisioning record (the intentional,
# account-linked Thing name). A unit's *reporting* identity (DynamoDB
# meter_last_seen.thingName) should match it. Test identities (SiteTest*, and
# rows flagged is_test) are excluded from the report per ops decision.

_TEST_THING_PREFIXES = ("sitetest", "testsite", "test-")


def _is_test_thing(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in _TEST_THING_PREFIXES)


def _identity_reconciliation():
    """Join meter_provisioning with meter_last_seen; categorize each unit.

    Returns (categories, seen_map). categories: match / mismatch / silent /
    unprovisioned. seen_map: meterId -> {thing, last_seen}.
    """
    from datetime import datetime, timezone
    from customer_api import get_connection

    prov = {}
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ON (meter_serial)
                   meter_serial, thing_name, account_number, site, pcb_mac, is_test, updated_at
            FROM meter_provisioning
            WHERE meter_serial IS NOT NULL AND meter_serial <> ''
            ORDER BY meter_serial, updated_at DESC NULLS LAST
            """
        )
        for s, t, a, site, pcb, is_test, _upd in cur.fetchall():
            prov[(s or "").strip()] = {
                "thing": t, "account": a, "site": site, "pcb": pcb,
                "is_test": bool(is_test),
            }

    seen = {}
    try:
        ddb = _client("dynamodb")
        start = None
        while True:
            kw = {"TableName": "meter_last_seen",
                  "ProjectionExpression": "meterId, thingName, last_seen, lastAcceptedTime"}
            if start:
                kw["ExclusiveStartKey"] = start
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                mid = it.get("meterId", {}).get("S")
                if mid:
                    seen[mid.strip()] = {
                        "thing": it.get("thingName", {}).get("S"),
                        "last_seen": it.get("last_seen", {}).get("S") or it.get("lastAcceptedTime", {}).get("S"),
                    }
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
    except Exception as exc:
        logger.warning("reconcile meter_last_seen scan failed: %s", exc)

    def norm(s):
        return (s or "").lstrip("0") or s
    seen_norm = {norm(k): v for k, v in seen.items()}

    match, mismatch, silent = [], [], []
    for serial, p in prov.items():
        if p["is_test"] or _is_test_thing(p["thing"]):
            continue
        s = seen.get(serial) or seen_norm.get(norm(serial))
        if not s:
            silent.append({"meter_serial": serial, **p})
        elif (s["thing"] or "") == (p["thing"] or ""):
            match.append({"meter_serial": serial, **p, "last_seen": s["last_seen"]})
        else:
            mismatch.append({"meter_serial": serial, **p,
                             "reporting_thing": s["thing"], "last_seen": s["last_seen"]})

    prov_norms = {norm(k) for k in prov}
    unprovisioned = [
        {"meter_serial": k, "reporting_thing": v["thing"], "last_seen": v["last_seen"]}
        for k, v in seen.items()
        if k not in prov and norm(k) not in prov_norms and not _is_test_thing(v["thing"])
    ]
    return {"match": match, "mismatch": mismatch, "silent": silent,
            "unprovisioned": unprovisioned}, seen


@router.get("/reconcile-identities")
def reconcile_identities(_user: CurrentUser = Depends(require_employee)):
    """Categorized identity reconciliation report (match / mismatch / silent /
    unprovisioned). Test identities excluded. Read-only; employee-readable."""
    cats, _seen = _identity_reconciliation()
    return {
        "match_count": len(cats["match"]),
        "mismatch_count": len(cats["mismatch"]),
        "silent_count": len(cats["silent"]),
        "unprovisioned_count": len(cats["unprovisioned"]),
        **cats,
    }


def _parse_seen_dt(ts):
    from datetime import datetime, timezone
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(str(ts), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@router.post("/harmonize-identities")
def harmonize_identities(
    online_window_minutes: int = 15,
    user: CurrentUser = Depends(CC_ADMIN_GATE),
):
    """Auto-harmonize identity mismatches: for every mismatched unit that is
    currently online (reported within `online_window_minutes`), publish
    ``cfg/identity`` to its *current* client id to rename it to the canonical
    (provisioning-record) Thing, and lodge each action in the mutation log.

    Works over MQTT without re-flashing because the DevicePolicy allows
    ``iot:Connect`` on a wildcard resource (any client id). Offline units are
    reported but skipped (rename only lands on a connected device).
    """
    from datetime import datetime, timezone, timedelta
    cats, _seen = _identity_reconciliation()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=online_window_minutes)

    renamed, skipped_offline, failed = [], [], []
    for row in cats["mismatch"]:
        serial = row["meter_serial"]
        canonical = row["thing"]
        current = row["reporting_thing"]
        seen_dt = _parse_seen_dt(row.get("last_seen"))
        if not (seen_dt and seen_dt >= cutoff):
            skipped_offline.append({"meter_serial": serial, "canonical": canonical,
                                    "reporting": current, "last_seen": row.get("last_seen")})
            continue
        try:
            _validate_thing_name(canonical)
            mac = _norm_mac(row.get("pcb"))
            site = (row.get("site") or "").strip().upper()
            cert_arn, cert_id, cert_pem, key_pem = _issue_cert_and_payload(
                canonical,
                {"meter_serial": serial, "account": row.get("account") or "",
                 "site": site, "legacy_id": current},
                DEFAULT_POLICY,
            )
            identity_payload = {"thing_name": canonical, "version": 2,
                                "cert_pem": cert_pem, "key_pem": key_pem}
            topic = IDENTITY_TOPIC_FMT.format(client_id=current)
            iotdata = _client("iot-data")
            iotdata.publish(topic=topic, qos=1,
                            payload=json.dumps(identity_payload).encode("utf-8"))
            # Lodge in the mutation log (audit trail for the auto-rename).
            try:
                from customer_api import get_connection
                with get_connection() as conn:
                    try_log_mutation(
                        user, "update", "meter_provisioning", canonical,
                        new_values={"thing_name": canonical, "from_client_id": current,
                                    "meter_serial": serial, "cert_id": cert_id},
                        metadata={"kind": "auto_harmonize_identity",
                                  "endpoint": "POST /api/provisioning/harmonize-identities",
                                  "topic": topic, "pcb_mac": mac},
                        conn=conn,
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("harmonize audit log failed: %s", exc)
            renamed.append({"meter_serial": serial, "from": current, "to": canonical,
                            "topic": topic})
        except Exception as exc:  # noqa: BLE001
            logger.exception("harmonize failed for %s -> %s", current, canonical)
            failed.append({"meter_serial": serial, "from": current, "to": canonical,
                           "error": str(exc)})

    return {
        "renamed": renamed,
        "renamed_count": len(renamed),
        "skipped_offline": skipped_offline,
        "skipped_offline_count": len(skipped_offline),
        "failed": failed,
        "failed_count": len(failed),
        "note": "Online mismatches renamed to canonical via cfg/identity (logged in "
                "mutation log). Offline units must be renamed when they next connect "
                "or via the provisioning station.",
    }


IOT_INGEST_KEY = os.environ.get("IOT_INGEST_KEY", "1pwr-iot-ingest-2026")


@router.post("/rename-event")
def rename_event(
    payload: dict,
    x_iot_key: Optional[str] = Header(default=None, alias="X-IoT-Key"),
):
    """Lodge an applied identity rename in the mutation log.

    Called by the ingestion_gate Lambda (shared-secret X-IoT-Key header, same
    pattern as `/api/meters/reading`) after it publishes a queued `cfg/identity`
    rename to a unit that came online. This is the audit trail for the
    event-driven (no-cron) identity reconciliation.
    """
    if x_iot_key != IOT_INGEST_KEY:
        raise HTTPException(status_code=403, detail="Invalid IoT key")

    meter_id = str(payload.get("meter_id") or "")
    from_thing = str(payload.get("from_thing") or "")
    to_thing = str(payload.get("to_thing") or "")
    status = str(payload.get("status") or "rename_published")
    if not (meter_id and to_thing):
        raise HTTPException(status_code=400, detail="meter_id and to_thing required")

    # System identity for the audit row (the actor is the ingestion Lambda).
    system_user = CurrentUser(
        user_type="employee", user_id="ingestion_lambda",
        role="system", name="Ingestion Lambda (auto-reconcile)",
    )
    try:
        from customer_api import get_connection
        with get_connection() as conn:
            try_log_mutation(
                system_user, "update", "meter_provisioning", to_thing,
                old_values={"thing_name": from_thing} if from_thing else None,
                new_values={"thing_name": to_thing, "meter_serial": meter_id},
                metadata={"kind": "auto_rename_applied", "status": status,
                          "source": "ingestion_gate Lambda", "meter_id": meter_id},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rename-event mutation log failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"mutation log failed: {exc}")

    return {"status": "logged", "meter_id": meter_id, "to_thing": to_thing}


@router.get("/fleet-live")
def fleet_live(_user: CurrentUser = Depends(CC_OPERATE_GATE)):
    """Live fleet status: which units are connected and/or providing telemetry.

    Joins three sources:
    - AWS IoT fleet index (`connectivity.connected`) — MQTT session up.
    - `meter_last_seen` (`lastAcceptedTime` = meter's sample time, `last_seen` =
      ingestion time) — last accepted reading.
    - `1meter_data` (latest `sample_time`) — actual telemetry recency.

    A unit is "operational" if it's connected OR has recent telemetry.
    """
    ddb = _client("dynamodb")
    iot = _client("iot")

    # 1. Fleet index connectivity (OneMeter* + MAK-GW*)
    connected = {}
    for pattern in ("thingName:OneMeter*", "thingName:MAK-GW*"):
        try:
            resp = iot.search_index(queryString=pattern, maxResults=200)
            for t in resp.get("things", []):
                name = t.get("thingName")
                conn = t.get("connectivity", {})
                connected[name] = {
                    "connected": conn.get("connected", False),
                    "connect_ts": conn.get("timestamp"),
                    "disconnect_reason": conn.get("disconnectReason"),
                }
        except Exception as exc:
            logger.warning("fleet index query failed for %s: %s", pattern, exc)

    # 2. meter_last_seen (last accepted reading + ingestion time)
    last_seen = {}
    start = None
    try:
        while True:
            kw = {"TableName": "meter_last_seen",
                  "ProjectionExpression": "meterId, thingName, lastAcceptedTime, last_seen, EnergyActive, Relay"}
            if start:
                kw["ExclusiveStartKey"] = start
            resp = ddb.scan(**kw)
            for it in resp.get("Items", []):
                def g(k):
                    v = it.get(k, {})
                    return list(v.values())[0] if v else None
                tn = g("thingName") or ""
                last_seen[tn] = {
                    "meter_id": g("meterId"),
                    "last_accepted": g("lastAcceptedTime"),
                    "last_seen": g("last_seen"),
                    "energy": g("EnergyActive"),
                    "relay": g("Relay"),
                }
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
    except Exception as exc:
        logger.warning("meter_last_seen scan failed: %s", exc)

    # 3. Latest telemetry per meter from 1meter_data (sample_time)
    telemetry = {}
    try:
        for tn, info in last_seen.items():
            mid = info.get("meter_id")
            if not mid:
                continue
            r = ddb.query(
                TableName="1meter_data",
                KeyConditionExpression="device_id = :m",
                ExpressionAttributeValues={":m": {"S": mid}},
                ScanIndexForward=False,
                Limit=1,
            )
            items = r.get("Items", [])
            if items:
                it = items[0]
                telemetry[tn] = {
                    "latest_sample": list(it.get("sample_time", {}).values())[0] if it.get("sample_time") else None,
                    "power": list(it.get("Power", {}).values())[0] if it.get("Power") else None,
                    "fw": list(it.get("FirmwareVersion", {}).values())[0] if it.get("FirmwareVersion") else None,
                }
    except Exception as exc:
        logger.warning("1meter_data query failed: %s", exc)

    # 4. Build unified rows
    rows = []
    for tn in sorted(set(connected.keys()) | set(last_seen.keys())):
        conn = connected.get(tn, {})
        ls = last_seen.get(tn, {})
        tele = telemetry.get(tn, {})
        is_operational = conn.get("connected") or bool(tele.get("latest_sample"))
        rows.append({
            "thing_name": tn,
            "connected": conn.get("connected"),
            "connect_ts": conn.get("connect_ts"),
            "meter_id": ls.get("meter_id"),
            "last_accepted": ls.get("last_accepted"),
            "last_seen": ls.get("last_seen"),
            "latest_sample": tele.get("latest_sample"),
            "power": tele.get("power"),
            "fw": tele.get("fw"),
            "operational": is_operational,
        })

    # Sort: connected first, then by latest_sample desc
    def sort_key(r):
        return (not r["connected"], r.get("latest_sample") or "", r["thing_name"])
    rows.sort(key=sort_key)

    operational = sum(1 for r in rows if r["operational"])
    connected_count = sum(1 for r in rows if r["connected"])
    return {
        "total_things": len(rows),
        "operational": operational,
        "connected": connected_count,
        "units": rows,
    }


@router.get("/registry")
def list_registry(_user: CurrentUser = Depends(CC_OPERATE_GATE)):
    """List the provisioning registry (DynamoDB scan), newest first."""
    ddb = _client("dynamodb")
    items, start = [], None
    try:
        while True:
            kwargs = {"TableName": REGISTRY_TABLE}
            if start:
                kwargs["ExclusiveStartKey"] = start
            resp = ddb.scan(**kwargs)
            items += resp.get("Items", [])
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"registry scan failed: {exc}") from exc

    rows = []
    for it in items:
        rows.append({k: list(v.values())[0] for k, v in it.items()})
    rows.sort(key=lambda r: r.get("provisioned_at") or r.get("claimed_at") or "", reverse=True)
    return {"count": len(rows), "rows": rows}
