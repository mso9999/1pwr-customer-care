"""
MAK meter connectivity diagnostic endpoint.
Queries 1PDB prototype_meter_state + AWS IoT Fleet Indexing and returns
combined connectivity status per meter as JSON.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from fastapi import APIRouter, Depends, HTTPException

from middleware import require_employee
from models import CurrentUser

logger = logging.getLogger("acdb-api.mak_connectivity")

router = APIRouter(prefix="/api/mak-connectivity", tags=["mak-connectivity"])

SITE = "MAK"
ONLINE_THRESHOLD_HOURS = 2
STALE_THRESHOLD_HOURS = 6
OFFLINE_THRESHOLD_HOURS = 30


def _query_1pdb_meters() -> List[Dict[str, Any]]:
    from customer_api import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT meter_id, account_number, last_seen_at,
                       last_energy_kwh, firmware_version, last_relay_status
                FROM prototype_meter_state
                WHERE meter_id LIKE '23%' OR account_number LIKE '%%MAK'
                ORDER BY last_seen_at DESC NULLS LAST
                """
            )
            rows = cur.fetchall()

    meters: List[Dict[str, Any]] = []
    for row in rows:
        meter_id, account, last_seen, energy, fw, relay = row
        hours_ago: Optional[float] = None
        status = "unknown"
        last_seen_iso: Optional[str] = None

        if last_seen is not None:
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            last_seen_iso = last_seen.isoformat()
            delta = datetime.now(timezone.utc) - last_seen
            hours_ago = round(delta.total_seconds() / 3600, 1)
            if hours_ago < ONLINE_THRESHOLD_HOURS:
                status = "online"
            elif hours_ago < STALE_THRESHOLD_HOURS:
                status = "stale"
            elif hours_ago < OFFLINE_THRESHOLD_HOURS:
                status = "offline"
            else:
                status = "offline_extended"

        meters.append({
            "meter_id": str(meter_id).strip() if meter_id else "",
            "account": str(account).strip() if account else "",
            "last_seen": last_seen_iso,
            "hours_ago": hours_ago,
            "status": status,
            "firmware": str(fw).strip() if fw else "",
            "energy_kwh": float(energy) if energy else None,
            "relay": relay,
        })
    return meters


def _query_aws_iot() -> Dict[str, Dict[str, Any]]:
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    iot = boto3.client("iot", region_name=region)

    results: Dict[str, Dict[str, Any]] = {}
    next_token: Optional[str] = None

    while True:
        kwargs: Dict[str, Any] = {
            "IndexName": "AWS_Things",
            "QueryString": f"attributes.site:{SITE}",
            "QueryVersion": "2017-09-30",
        }
        if next_token:
            kwargs["NextToken"] = next_token

        resp = iot.search_index(**kwargs)

        for thing in resp.get("things", []):
            thing_name = thing.get("thingName", "")
            connectivity = thing.get("connectivity", {})
            attributes = thing.get("attributes", {})

            results[thing_name] = {
                "thing_name": thing_name,
                "connected": connectivity.get("connected", False),
                "timestamp": connectivity.get("timestamp"),
                "disconnect_reason": connectivity.get("disconnectReason"),
                "disconnect_time": connectivity.get("disconnectTime"),
                "meter_serial": attributes.get("meter_serial", ""),
                "site": attributes.get("site", ""),
            }

        next_token = resp.get("nextToken")
        if not next_token:
            break

    return results


@router.get("")
def get_mak_connectivity(
    user: CurrentUser = Depends(require_employee),
):
    try:
        meters = _query_1pdb_meters()
    except Exception as exc:
        logger.exception("1PDB query failed")
        raise HTTPException(status_code=500, detail=f"1PDB query failed: {exc}")

    iot_data: Dict[str, Dict[str, Any]] = {}
    iot_error: Optional[str] = None
    try:
        iot_data = _query_aws_iot()
    except Exception as exc:
        logger.exception("AWS IoT query failed")
        iot_error = str(exc)

    combined: List[Dict[str, Any]] = []
    for m in meters:
        iot_info = iot_data.get(m["meter_id"], {})
        combined.append({
            **m,
            "iot_connected": iot_info.get("connected"),
            "iot_disconnect_reason": iot_info.get("disconnect_reason"),
            "iot_disconnect_time": iot_info.get("disconnect_time"),
        })

    iot_only = [
        v for k, v in iot_data.items()
        if k not in {m["meter_id"] for m in meters}
    ]

    summary = {
        "total_meters": len(meters),
        "online": sum(1 for m in meters if m["status"] == "online"),
        "stale": sum(1 for m in meters if m["status"] == "stale"),
        "offline": sum(1 for m in meters if m["status"] in ("offline", "offline_extended")),
        "never_reported": sum(1 for m in meters if m["status"] == "unknown"),
        "iot_things": len(iot_data),
        "iot_connected": sum(1 for v in iot_data.values() if v.get("connected")),
    }

    return {
        "meters": combined,
        "iot_only": iot_only,
        "summary": summary,
        "iot_error": iot_error,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
