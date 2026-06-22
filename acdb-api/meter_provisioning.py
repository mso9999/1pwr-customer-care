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
and customer **accounts**, so the Thing name ``<SITE>-<account>`` is canonical by
construction and cannot drift into ad-hoc ``TestSite*`` names. The provisioning
registry (DynamoDB ``1meter_provisioning_registry``) stays the single source of
truth for PCB-MAC -> Thing, shared with the bench/HQ PowerShell path.

Flow
----
1. ``GET  /api/provisioning/site-codes``      -> canonical site dropdown.
2. ``POST /api/provisioning/things``          -> validate -> claim registry ->
   create Thing (+type+attrs) -> issue cert -> attach policy -> record cert ->
   return the firmware bootstrap payload (thing_name/ssid/password/version/
   cert_pem/key_pem) for the operator to POST to the device local API.
3. ``GET  /api/provisioning/registry``        -> list registry rows.
4. ``POST /api/provisioning/rotate``          -> publish ``cfg/identity`` to an
   already-online unit's *current* client id to rename it in place (migration of
   existing ``TestSite*`` / ``OneMeterN`` units), matching the firmware
   ``oneMeter/<clientId>/cfg/identity`` handler.

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

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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

# Names we must never silently overwrite from the GUI. Bench/test identities
# belong to the HQ PowerShell flow; ad-hoc field names are exactly what this
# system exists to retire.
_BENCH_PREFIXES = ("HQTEST", "TEST-", "TESTSITE")

PROVISIONING_ROLES = (CCRole.superadmin, CCRole.onm_team)


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


def _registry_claim(mac: str, thing: str, *, site: str, operator: str):
    """Atomic claim with the same guarantees as provisioning_registry.py claim."""
    ddb = _client("dynamodb")

    for it in _registry_get_by_thing(thing):
        if it.get("pcb_mac", {}).get("S") != mac:
            raise HTTPException(
                status_code=409,
                detail=f"Thing '{thing}' is already claimed by PCB "
                       f"{it['pcb_mac']['S']} (not {mac}).",
            )

    existing = _registry_get_by_mac(mac)
    if existing and existing.get("thing_name", {}).get("S") != thing:
        raise HTTPException(
            status_code=409,
            detail=f"PCB {mac} is already bound to Thing "
                   f"'{existing['thing_name']['S']}', not '{thing}'.",
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
    try:
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


class RotateRequest(BaseModel):
    current_client_id: str = Field(..., description="The unit's CURRENT MQTT client id (e.g. TestSite4)")
    site_code: str
    account: str
    meter_serial: str
    pcb_mac: str
    policy_name: str = Field(default="")
    version: int = Field(default=2, ge=1, description="Bump > current so the device accepts the new identity")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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
            try_log_mutation(
                user, "create", "iot_provisioning", thing,
                new_values={"thing_name": thing, "meter_serial": payload.meter_serial,
                            "account": payload.account, "site": site, "cert_id": cert_id},
                metadata={"kind": "provision_thing", "endpoint": "POST /api/provisioning/things",
                          "pcb_mac": mac, "policy": policy},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - audit must never block provisioning
        logger.warning("provision audit log failed: %s", exc)

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


@router.post("/rotate")
def rotate_identity(
    payload: RotateRequest,
    user: CurrentUser = Depends(require_role(*PROVISIONING_ROLES)),
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

    _registry_claim(mac, new_thing, site=site, operator=f"cc:{user.user_id}")

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
            try_log_mutation(
                user, "update", "iot_provisioning", new_thing,
                new_values={"thing_name": new_thing, "from_client_id": payload.current_client_id,
                            "meter_serial": payload.meter_serial, "cert_id": cert_id},
                metadata={"kind": "rotate_identity", "endpoint": "POST /api/provisioning/rotate",
                          "topic": topic, "pcb_mac": mac},
                conn=conn,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rotate audit log failed: %s", exc)

    return {
        "new_thing_name": new_thing,
        "from_client_id": payload.current_client_id,
        "published_topic": topic,
        "certificate_id": cert_id,
        "ack_topic": f"oneMeter/{payload.current_client_id}/cfg/identity/ack",
        "note": "Device will reboot and reconnect under the new Thing. Confirm via the ack topic and fleet index.",
    }


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
