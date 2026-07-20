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

The bootstrap / cfg-identity payload schema is dictated by the firmware
(``local_api_server.c`` and ``device_control.c``): keys ``thing_name``, ``ssid``,
``password``, ``version``, ``cert_pem``, ``key_pem``.

Boto3 credentials: the CC host's IAM role/profile must allow the IoT control
plane (``iot:CreateThing``, ``CreateThingType``, ``DescribeThing*``,
``CreateKeysAndCertificate``, ``AttachThingPrincipal``, ``AttachPolicy``),
``iot-data:Publish`` (already used by relay_control), and DynamoDB read/write on
``1meter_provisioning_registry``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from middleware import require_role
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

# Names we must never silently overwrite from the GUI. Bench/test identities
# belong to the HQ PowerShell flow; ad-hoc field names are exactly what this
# system exists to retire.
_BENCH_PREFIXES = ("HQTEST", "TEST-", "TESTSITE")

PROVISIONING_ROLES = (CCRole.superadmin, CCRole.onm_team, CCRole.engineering)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    if site not in ALL_SITE_ABBREV:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown site code '{site}'. It must be a canonical CC site "
                   f"code (one of: {', '.join(sorted(ALL_SITE_ABBREV))}).",
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


def _registry_claim(mac: str, thing: str, *, site: str, operator: str, allow_rebind: bool = False):
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

    item = {
        "pcb_mac": {"S": mac},
        "thing_name": {"S": thing},
        "is_test": {"BOOL": False},
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
            # Atomic per-site gateway sequence allocator (MAK-GW-0007 ...).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gateway_pool_seq (
                    site       VARCHAR(16) PRIMARY KEY,
                    last_seq   INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
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
                              box_label=None):
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
             cert_arn, status, legacy_id, fw_version, provisioned_by, box_label, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
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
            updated_at     = NOW()
        """,
        (thing, meter_serial, pcb_mac, site, account, cert_id, cert_arn,
         status, legacy_id, fw_version, operator, box_label),
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
    wifi_password: str = Field(..., max_length=128)
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
    wifi_password: str = Field(..., max_length=128)
    softap_ssid: Optional[str] = Field(default=None, max_length=64,
                                        description="Optional SoftAP SSID for the device hotspot")
    softap_password: Optional[str] = Field(default=None, max_length=128,
                                            description="Optional SoftAP password for the device hotspot")
    policy_name: str = Field(default="")
    version: int = Field(default=1, ge=1)


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
    wifi_password: str = Field(..., max_length=128)
    softap_ssid: Optional[str] = Field(default=None, max_length=64)
    softap_password: Optional[str] = Field(default=None, max_length=128)
    version: int = Field(default=1, ge=1, description="Monotonic version — must be higher than the device's current config version")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


STATION_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisioning_station_dist")


@router.get("/station/download")
def download_station(_user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES))):
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
        headers={"Content-Disposition": "attachment; filename=provisioning-station.zip"},
    )


@router.get("/site-codes", response_model=list[SiteCode])
def list_site_codes(_user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES))):
    """Canonical site codes from CC's country config — the only valid prefixes."""
    out = []
    for code, name in sorted(ALL_SITE_ABBREV.items()):
        out.append(SiteCode(
            code=code,
            name=name,
            district=ALL_SITE_DISTRICTS.get(code),
            country=get_country_for_site(code),
        ))
    return out


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
    user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES)),
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
    user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES)),
):
    """Batch-provision virgin gateways for a site (account-free, gateway-pool names).

    Allocates a stable ``<SITE>-GW-<seq>`` Thing per unit, issues a cert, claims
    the registry by PCB MAC, records to 1PDB (status='provisioned', no account),
    and returns the device bootstrap payload per unit for the provisioning
    station to deliver on the local network.
    """
    site = payload.site_code.strip().upper()
    if site not in ALL_SITE_ABBREV:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown site code '{site}'. Must be canonical "
                   f"(one of: {', '.join(sorted(ALL_SITE_ABBREV))}).",
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
            _registry_claim(mac, thing, site=site, operator=f"cc:{user.user_id}")
            attrs = {"site": site, "role": "gateway", "legacy_id": ""}
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
                    )
                    try_log_mutation(
                        user, "create", "meter_provisioning", thing,
                        new_values={"thing_name": thing, "site": site, "pcb_mac": mac,
                                    "cert_id": cert_id, "box_label": unit.box_label},
                        metadata={"kind": "provision_gateway", "endpoint": "POST /api/provisioning/gateways"},
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
        "gateways": results,
        "errors": errors,
    }


@router.post("/reconcile")
def reconcile_from_telemetry(_user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES))):
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
    from customer_api import get_connection
    with get_connection() as conn:
        cur = conn.cursor()
        for thing, (serial, _ts) in seen.items():
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
    return {"matched_things": len(seen), "rows_updated": updated}


@router.post("/rotate")
def rotate_identity(
    payload: RotateRequest,
    user: CurrentUser = Depends(require_role(CCRole.superadmin)),
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
    user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES)),
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
                "UPDATE meter_provisioning SET updated_at = NOW() WHERE thing_name = %s",
                (thing,),
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
    _user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES)),
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


@router.get("/registry")
def list_registry(_user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES))):
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
