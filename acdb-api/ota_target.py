"""Operator firmware targets from the meters map and list.

Factory promote ships the newest fleet image in S3, even when the site-release
row is older. This module never writes that table. The map queue is the
exception: an operator can name an older library version and that version is
what gets sent. It queues one gateway at a time per site and creates a
single-Thing AWS IoT job only when that gateway is online and the site has
no other download in progress.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("cc-api.ota-target")

OTA_BUCKET = os.environ.get("ONEMETER_OTA_BUCKET", "1pwr-ota-firmware")
OTA_ACCOUNT_ID = os.environ.get("IOT_ACCOUNT_ID", "758201218523")
OTA_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
OTA_SIGNING_PROFILE = os.environ.get("ONEMETER_OTA_SIGNING_PROFILE", "1PWR_OTA_ESP32_v2")
OTA_ROLE_ARN = os.environ.get(
    "ONEMETER_OTA_ROLE_ARN",
    "arn:aws:iam::758201218523:role/1pwr-ota-service-role",
)
IOT_ENDPOINT = os.environ.get("IOT_ENDPOINT", "a3p95svnbmzyit-ats.iot.us-east-1.amazonaws.com")

FACTORY_BASELINE = (1, 1, 56)
KICK_AGAIN_S = 900
ADVISORY_LOCK = 117608
OPEN_STATUSES = ("pending", "active", "held")
FAIL_STATUSES = {"FAILED", "REJECTED", "TIMED_OUT", "CANCELED", "REMOVED"}
FLEET_KEY = re.compile(
    r"^firmware-releases/v(\d+\.\d+\.\d+)/Fleet\d+/FeaturedFreeRTOSIoTIntegration\.bin$"
)
THING_SITE = re.compile(r"^([A-Za-z0-9]+)-GW-")

QUEUE_TABLE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS onemeter_ota_queue (
        id                   BIGSERIAL PRIMARY KEY,
        batch_id             UUID NOT NULL,
        site_code            VARCHAR(16) NOT NULL,
        thing_name           VARCHAR(128) NOT NULL,
        meter_ids            TEXT[] NOT NULL DEFAULT '{}',
        target_version       VARCHAR(32) NOT NULL,
        artifact_key         TEXT NOT NULL,
        artifact_version_id  TEXT NOT NULL,
        current_fw           VARCHAR(32),
        status               VARCHAR(16) NOT NULL DEFAULT 'pending',
        ota_update_id        TEXT,
        job_id               TEXT,
        detail               TEXT,
        created_by           TEXT,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at           TIMESTAMPTZ,
        finished_at          TIMESTAMPTZ,
        kicked_at            TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_onemeter_ota_queue_site_status
        ON onemeter_ota_queue (site_code, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_onemeter_ota_queue_batch
        ON onemeter_ota_queue (batch_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS onemeter_ota_queue_one_open
        ON onemeter_ota_queue (thing_name)
        WHERE status IN ('pending', 'active', 'held')
    """,
)

INSERT_SQL = """
    INSERT INTO onemeter_ota_queue (
        batch_id, site_code, thing_name, meter_ids, target_version,
        artifact_key, artifact_version_id, current_fw, status, created_by
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
"""

_library_cache: dict = {}
_advancer_started = False


class TargetRefused(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def version_tuple(value) -> tuple | None:
    match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(value or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def site_of_thing(thing: str | None, community: str | None = None) -> str:
    match = THING_SITE.match(thing or "")
    if match:
        return match.group(1).upper()
    return str(community or "").strip().upper()


def meter_id_keys(meter_id: str) -> list[str]:
    """Forms a meter serial takes in CC and in meter_last_seen.

    The map matches the raw id, the id without leading zeros, and the
    12-digit form. A lookup of only the CC id misses a padded Dynamo key
    and then targets the provisioning record, which can be an old gateway.
    """
    text = str(meter_id or "").strip()
    keys: list[str] = []
    for candidate in (text, text.lstrip("0"), text.zfill(12)):
        if candidate and candidate not in keys:
            keys.append(candidate)
    return keys


def should_kick(*, kicked_at: datetime | None, connected: bool, now: datetime) -> bool:
    """A manual update nudges a connected gateway as soon as its job exists.

    Firmware before 1.1.72 only asks AWS for a job on MQTT connect, so a job
    created while the gateway is already connected stays queued until something
    drops that session. Kick once immediately, then again every 15 minutes
    while the execution is still queued.
    """
    if not connected:
        return False
    if kicked_at is None:
        return True
    return now - kicked_at >= timedelta(seconds=KICK_AGAIN_S)


def present_map_ota(aws: dict, queue: dict | None) -> dict:
    """What the meter popup should say.

    An AWS job wins. A CC queue row with no job yet still shows as queued,
    so the popup is not blank while the gateway is offline or the job is
    being created.
    """
    if aws.get("in_flight"):
        phase = "downloading" if aws.get("status") == "IN_PROGRESS" else "starting"
        return {**aws, "phase": phase}
    if not queue:
        return aws
    queue_status = queue.get("queue_status") or queue.get("status")
    if queue_status == "pending":
        phase = "waiting_online"
    elif queue_status == "held":
        phase = "held"
    else:
        phase = "starting"
    return {
        "thing_name": aws.get("thing_name"),
        "in_flight": True,
        "status": "QUEUED",
        "target_version": queue.get("target_version"),
        "percent": 0,
        "phase": phase,
    }


def prefer_thing(seen: str | None, prov: str | None, link: str | None) -> str | None:
    """Same order as the fleet map: last telemetry, then provisioning, then the pole link."""
    for candidate in (seen, prov, link):
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def fleet_images_from_versions(versions: list[dict]) -> list[dict]:
    """Newest fleet image of each version. Per-Thing builds are ignored.

    A version at or below 1.1.56 is listed and marked not selectable. MQTT OTA
    cannot finish on that baseline, and we do not roll a gateway back onto it.
    """
    best: dict[str, dict] = {}
    for row in versions or []:
        if row.get("IsDeleteMarker"):
            continue
        key = str(row.get("Key") or "")
        match = FLEET_KEY.match(key)
        if not match:
            continue
        version = match.group(1)
        previous = best.get(version)
        stamp = row.get("LastModified") or datetime.min.replace(tzinfo=timezone.utc)
        if previous is None or stamp >= previous["_stamp"]:
            best[version] = {
                "_stamp": stamp,
                "version": version,
                "artifact_key": key,
                "artifact_version_id": row.get("VersionId"),
            }
    images = []
    for version, row in best.items():
        parsed = version_tuple(version)
        images.append({
            "version": version,
            "artifact_key": row["artifact_key"],
            "artifact_version_id": row["artifact_version_id"],
            "selectable": bool(parsed and parsed > FACTORY_BASELINE),
        })
    images.sort(key=lambda item: version_tuple(item["version"]) or (0, 0, 0), reverse=True)
    return images


def pin_release_to_latest(release: dict, images: list[dict]) -> dict:
    """Copy a site release onto the newest selectable fleet image.

    Automatic promotion uses this so a stale ``onemeter_ota_site_releases``
    row cannot ship an older build. The map queue does not call it: there
    the operator's chosen version is the one that is sent.
    """
    latest = None
    latest_tuple = None
    for image in images or []:
        if not image.get("selectable"):
            continue
        parsed = version_tuple(image.get("version"))
        if parsed is None:
            continue
        if latest_tuple is None or parsed > latest_tuple:
            latest = image
            latest_tuple = parsed
    if latest is None:
        raise TargetRefused("No selectable fleet image is published.")
    pinned = dict(release)
    pinned["target_firmware_version"] = latest["version"]
    pinned["artifact_key"] = latest["artifact_key"]
    pinned["artifact_version_id"] = latest["artifact_version_id"]
    return pinned


def _is_s3_access_denied(exc: BaseException) -> bool:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "")
    text = f"{code} {exc}".lower()
    return "accessdenied" in text or "not authorized" in text or "access denied" in text


def _list_version_rows(s3, bucket: str) -> list[dict]:
    rows: list[dict] = []
    key_marker = None
    version_marker = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": "firmware-releases/"}
        if key_marker:
            kwargs["KeyMarker"] = key_marker
            kwargs["VersionIdMarker"] = version_marker
        resp = s3.list_object_versions(**kwargs)
        rows.extend(resp.get("Versions") or [])
        if not resp.get("IsTruncated"):
            break
        key_marker = resp.get("NextKeyMarker")
        version_marker = resp.get("NextVersionIdMarker")
        if not key_marker:
            break
    return rows


def _list_current_fleet_rows(s3, bucket: str) -> list[dict]:
    """Current Fleet* objects when ``s3:ListBucketVersions`` is denied.

    Uses ``s3:ListBucket`` plus ``head_object`` so the current VersionId is
    still available for an OTA job.
    """
    rows: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": "firmware-releases/"}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            key = str(obj.get("Key") or "")
            if not FLEET_KEY.match(key):
                continue
            head = s3.head_object(Bucket=bucket, Key=key)
            rows.append({
                "Key": key,
                "VersionId": head.get("VersionId"),
                "LastModified": obj.get("LastModified") or head.get("LastModified"),
                "IsDeleteMarker": False,
            })
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return rows


def library_from_s3(s3, bucket: str = OTA_BUCKET, *, use_cache: bool = True) -> list[dict]:
    now = time.time()
    cached = _library_cache.get(bucket)
    if use_cache and cached and now - cached[0] < 300:
        return cached[1]
    try:
        rows = _list_version_rows(s3, bucket)
    except Exception as exc:
        if not _is_s3_access_denied(exc):
            raise
        logger.warning(
            "ListObjectVersions denied on %s; listing current fleet objects: %s",
            bucket, exc,
        )
        rows = _list_current_fleet_rows(s3, bucket)
    images = fleet_images_from_versions(rows)
    _library_cache[bucket] = (now, images)
    return images


def classify_gateway(current_fw: str | None, target_version: str | None, thing: str | None) -> str:
    if not thing:
        return "no_gateway"
    current = version_tuple(current_fw)
    target = version_tuple(target_version) if target_version else None
    if current is not None and current <= FACTORY_BASELINE:
        return "too_old"
    if target is None:
        return "update"
    if current is not None and current == target:
        return "already"
    if current is not None and current > target:
        return "rollback"
    return "update"


def build_preview(rows: list[dict], target_version: str | None) -> dict:
    """Collapse meters that share a gateway. ``rows`` are already resolved."""
    groups: dict[str, dict] = {}
    skipped = []
    for row in rows:
        thing = row.get("thing_name")
        if not thing:
            skipped.append({
                "meter_id": row.get("meter_id"),
                "thing_name": None,
                "reason": "no_gateway",
            })
            continue
        group = groups.get(thing)
        if group is None:
            group = {
                "thing_name": thing,
                "site_code": site_of_thing(thing, row.get("site")),
                "meter_ids": [],
                "current_fw": None,
            }
            groups[thing] = group
        meter_id = row.get("meter_id")
        if meter_id and meter_id not in group["meter_ids"]:
            group["meter_ids"].append(meter_id)
        if not group["current_fw"] and row.get("fw_version"):
            group["current_fw"] = row.get("fw_version")
    gateways = []
    for group in groups.values():
        action = classify_gateway(group["current_fw"], target_version, group["thing_name"])
        gateways.append({**group, "action": action})
    gateways.sort(key=lambda item: item["thing_name"])
    return {
        "gateways": gateways,
        "skipped": skipped,
        "rollback": any(item["action"] == "rollback" for item in gateways),
        "target_version": target_version,
    }


def rows_to_queue(preview: dict, artifact: dict, confirm_version: str | None) -> list[dict]:
    """Gateways that should become pending rows. Refuses an unconfirmed rollback."""
    target = str(artifact.get("version") or "")
    if preview.get("rollback") and (confirm_version or "") != target:
        raise TargetRefused("Type the version to confirm a rollback.")
    if not artifact.get("selectable"):
        raise TargetRefused(f"{target} cannot be installed over MQTT.")
    queued = []
    for gateway in preview.get("gateways") or []:
        if gateway["action"] not in ("update", "rollback"):
            continue
        queued.append({
            "site_code": gateway["site_code"],
            "thing_name": gateway["thing_name"],
            "meter_ids": gateway["meter_ids"],
            "target_version": target,
            "artifact_key": artifact["artifact_key"],
            "artifact_version_id": artifact["artifact_version_id"],
            "current_fw": gateway.get("current_fw"),
        })
    return queued


def plan_advance(rows: list[dict], online: set[str], foreign_sites: set[str]) -> list[dict]:
    """At most one pending gateway per site, and only if it is online.

    A site with an active row, a held row, or someone else's in-progress job
    is left alone. Offline rows stay pending.
    """
    active = {row["site_code"] for row in rows if row["status"] == "active"}
    held = {row["site_code"] for row in rows if row["status"] == "held"}
    pending = [row for row in rows if row["status"] == "pending"]
    pending.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)))
    starts = []
    claimed = set()
    for row in pending:
        site = row["site_code"]
        if site in claimed or site in active or site in held or site in foreign_sites:
            continue
        if row["thing_name"] not in online:
            continue
        starts.append(row)
        claimed.add(site)
    return starts


def execution_outcome(status: str | None) -> str:
    if status == "SUCCEEDED":
        return "succeeded"
    if status in FAIL_STATUSES:
        return "failed"
    return "active"


def make_ota_id(thing: str, version: str, now: datetime) -> str:
    compact = version.replace(".", "-")
    stamp = now.strftime("%Y%m%d%H%M%S")
    return f"1m-target-{compact}-{thing}-{stamp}"[:64]


def operator_ota_kwargs(
    *,
    ota_id: str,
    thing: str,
    site: str,
    version: str,
    bucket: str,
    key: str,
    version_id: str,
    account_id: str = OTA_ACCOUNT_ID,
    region: str = OTA_REGION,
    role_arn: str = OTA_ROLE_ARN,
    signing_profile: str = OTA_SIGNING_PROFILE,
) -> dict:
    signed_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-")
    signing = {
        "startSigningJobParameter": {
            "signingProfileName": signing_profile,
            "destination": {
                "s3Destination": {
                    "bucket": bucket,
                    "prefix": f"signed/operator-target/{signed_version}",
                }
            },
            "signingProfileParameter": {"certificatePathOnDevice": "/"},
        }
    }
    return {
        "otaUpdateId": ota_id,
        "description": f"1Meter operator target {thing} to {version}",
        "targets": [f"arn:aws:iot:{region}:{account_id}:thing/{thing}"],
        "protocols": ["MQTT"],
        "targetSelection": "SNAPSHOT",
        "files": [{
            "fileName": "FeaturedFreeRTOSIoTIntegration.bin",
            "fileType": 0,
            "fileVersion": version,
            "fileLocation": {"s3Location": {"bucket": bucket, "key": key, "version": version_id}},
            "codeSigning": signing,
            "attributes": {
                "workflow": "operator_target",
                "target_version": version,
                "site": site,
            },
        }],
        "roleArn": role_arn,
        "awsJobExecutionsRolloutConfig": {"maximumPerMinute": 1},
    }


def _aware(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def kick_gateway(thing: str) -> None:
    """Take the MQTT client id for one second so a connected gateway polls jobs."""
    from awscrt import auth, io
    from awsiot import mqtt_connection_builder

    conn = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=IOT_ENDPOINT,
        region=OTA_REGION,
        credentials_provider=auth.AwsCredentialsProvider.new_default_chain(
            io.ClientBootstrap.get_or_create_static_default()
        ),
        client_id=thing,
        clean_session=True,
        keep_alive_secs=30,
    )
    conn.connect().result(timeout=15)
    time.sleep(1)
    conn.disconnect().result(timeout=10)


def _search_index(iot, query: str) -> list[dict]:
    things = []
    token = None
    while True:
        kwargs = {"queryString": query, "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = iot.search_index(**kwargs)
        things.extend(resp.get("things") or [])
        token = resp.get("nextToken")
        if not token:
            break
    return things


def connected_things(iot, sites: set[str]) -> set[str]:
    online = set()
    for site in sites:
        if not site:
            continue
        for thing in _search_index(iot, f"thingName:{site}-GW-*"):
            name = thing.get("thingName")
            if name and (thing.get("connectivity") or {}).get("connected"):
                online.add(name)
    return online


def foreign_busy_sites(iot, owned_job_ids: set[str]) -> set[str]:
    """Sites that already have a QUEUED or IN_PROGRESS execution we did not start."""
    sites = set()
    token = None
    while True:
        kwargs = {"status": "IN_PROGRESS", "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        resp = iot.list_jobs(**kwargs)
        for job in resp.get("jobs") or []:
            job_id = str(job.get("jobId") or "")
            if job_id in owned_job_ids:
                continue
            try:
                execs = iot.list_job_executions_for_job(jobId=job_id, maxResults=250)
            except Exception:  # noqa: BLE001
                logger.warning("Unable to list executions for %s", job_id)
                continue
            for item in execs.get("executionSummaries") or []:
                summary = item.get("jobExecutionSummary") or {}
                if summary.get("status") not in ("QUEUED", "IN_PROGRESS"):
                    continue
                thing = str(item.get("thingArn") or "").rsplit("/", 1)[-1]
                match = THING_SITE.match(thing)
                if match:
                    sites.add(match.group(1).upper())
        token = resp.get("nextToken")
        if not token:
            break
    return sites


def _fetch_rows(cur, where_sql: str, params: tuple) -> list[dict]:
    cur.execute(
        f"""
        SELECT id, batch_id, site_code, thing_name, meter_ids, target_version,
               artifact_key, artifact_version_id, current_fw, status,
               ota_update_id, job_id, detail, created_at, started_at, kicked_at
          FROM onemeter_ota_queue
         WHERE {where_sql}
         ORDER BY created_at, id
        """,
        params,
    )
    cols = [d[0] for d in cur.description]
    rows = []
    for raw in cur.fetchall():
        row = dict(zip(cols, raw))
        row["batch_id"] = str(row["batch_id"])
        row["meter_ids"] = list(row["meter_ids"] or [])
        rows.append(row)
    return rows


def _job_status(iot, job_id: str, thing: str) -> str | None:
    if not job_id:
        return None
    det = iot.describe_job_execution(jobId=job_id, thingName=thing)
    return (det.get("execution") or {}).get("status")


def advance_ota_queue(conn, iot, *, kick=kick_gateway, now: datetime | None = None) -> dict:
    """Poll active jobs and start at most one online gateway per idle site."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK,))
    locked = bool(cur.fetchone()[0])
    if not locked:
        return {"advanced": False, "reason": "locked", "started": []}
    started = []
    try:
        open_rows = _fetch_rows(cur, "status = ANY(%s)", (list(OPEN_STATUSES),))
        owned = {row["job_id"] for row in open_rows if row.get("job_id")}
        for row in open_rows:
            if row["status"] != "active":
                continue
            try:
                status = _job_status(iot, row.get("job_id") or "", row["thing_name"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("OTA status read failed for %s: %s", row["thing_name"], exc)
                continue
            outcome = execution_outcome(status)
            if outcome == "succeeded":
                cur.execute(
                    """
                    UPDATE onemeter_ota_queue
                       SET status = 'succeeded', finished_at = %s, detail = NULL
                     WHERE id = %s
                    """,
                    (now, row["id"]),
                )
            elif outcome == "failed":
                cur.execute(
                    """
                    UPDATE onemeter_ota_queue
                       SET status = 'failed', finished_at = %s, detail = %s
                     WHERE id = %s
                    """,
                    (now, status, row["id"]),
                )
                cur.execute(
                    """
                    UPDATE onemeter_ota_queue
                       SET status = 'held', detail = %s
                     WHERE site_code = %s AND status = 'pending'
                    """,
                    (f"Held after {row['thing_name']} {status}", row["site_code"]),
                )
            elif status == "QUEUED" and should_kick(
                kicked_at=_aware(row.get("kicked_at")),
                connected=row["thing_name"] in connected_things(iot, {row["site_code"]}),
                now=now,
            ):
                try:
                    kick(row["thing_name"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OTA kick failed for %s: %s", row["thing_name"], exc)
                else:
                    cur.execute(
                        "UPDATE onemeter_ota_queue SET kicked_at = %s WHERE id = %s",
                        (now, row["id"]),
                    )
        conn.commit()
        open_rows = _fetch_rows(cur, "status = ANY(%s)", (list(OPEN_STATUSES),))
        owned = {row["job_id"] for row in open_rows if row.get("job_id")}
        try:
            busy = foreign_busy_sites(iot, owned)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTA site guard failed: %s", exc)
            busy = {row["site_code"] for row in open_rows}
        pending_sites = {row["site_code"] for row in open_rows if row["status"] == "pending"}
        online = connected_things(iot, pending_sites) if pending_sites else set()
        for row in plan_advance(open_rows, online, busy):
            ota_id = make_ota_id(row["thing_name"], row["target_version"], now)
            job_id = f"AFR_OTA-{ota_id}"
            kwargs = operator_ota_kwargs(
                ota_id=ota_id,
                thing=row["thing_name"],
                site=row["site_code"],
                version=row["target_version"],
                bucket=OTA_BUCKET,
                key=row["artifact_key"],
                version_id=row["artifact_version_id"],
            )
            try:
                iot.create_ota_update(**kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OTA create failed for %s: %s", row["thing_name"], exc)
                cur.execute(
                    """
                    UPDATE onemeter_ota_queue
                       SET status = 'failed', finished_at = %s, detail = %s
                     WHERE id = %s
                    """,
                    (now, str(exc)[:500], row["id"]),
                )
                cur.execute(
                    """
                    UPDATE onemeter_ota_queue
                       SET status = 'held', detail = %s
                     WHERE site_code = %s AND status = 'pending'
                    """,
                    (f"Held after {row['thing_name']} create failed", row["site_code"]),
                )
                conn.commit()
                continue
            cur.execute(
                """
                UPDATE onemeter_ota_queue
                   SET status = 'active', ota_update_id = %s, job_id = %s,
                       started_at = %s, detail = NULL
                 WHERE id = %s AND status = 'pending'
                """,
                (ota_id, job_id, now, row["id"]),
            )
            cur.execute(
                """
                UPDATE meter_provisioning
                   SET ota_update_id = %s, ota_target_version = %s,
                       ota_status = 'QUEUED', ota_updated_at = %s, updated_at = %s
                 WHERE thing_name = %s
                """,
                (ota_id, row["target_version"], now, now, row["thing_name"]),
            )
            conn.commit()
            started.append(row["thing_name"])
            if should_kick(kicked_at=None, connected=True, now=now):
                try:
                    kick(row["thing_name"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OTA kick failed for %s: %s", row["thing_name"], exc)
                else:
                    cur.execute(
                        "UPDATE onemeter_ota_queue SET kicked_at = %s WHERE id = %s",
                        (now, row["id"]),
                    )
                    conn.commit()
        return {"advanced": True, "started": started}
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("OTA queue advisory unlock failed")


def resolve_meters(cur, ddb, meter_ids: list[str]) -> list[dict]:
    wanted = []
    seen = set()
    for meter_id in meter_ids:
        text = str(meter_id or "").strip()
        if text and text not in seen:
            seen.add(text)
            wanted.append(text)
    if not wanted:
        return []
    cur.execute(
        """
        SELECT m.meter_id, m.community,
               MAX(p.thing_name) AS prov_thing,
               MAX(gl.gateway_thing) AS link_thing,
               MAX(p.fw_version) AS prov_fw,
               MAX(s.firmware_version) AS reported_fw
          FROM meters m
          LEFT JOIN meter_provisioning p
            ON p.meter_serial = m.meter_id
            OR p.meter_serial = m.meter_number
            OR ltrim(p.meter_serial, '0') = ltrim(m.meter_id, '0')
          LEFT JOIN meter_gateway_link gl
            ON ltrim(gl.meter_serial, '0') = ltrim(m.meter_id, '0')
            OR ltrim(gl.meter_serial, '0') = ltrim(COALESCE(m.meter_number, ''), '0')
          LEFT JOIN prototype_meter_state s
            ON ltrim(s.meter_id, '0') = ltrim(m.meter_id, '0')
         WHERE (m.status IS NULL OR m.status <> 'decommissioned')
           AND m.meter_id = ANY(%s)
         GROUP BY m.meter_id, m.community
        """,
        (wanted,),
    )
    cols = [d[0] for d in cur.description]
    found = {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}
    telemetry = _last_seen_things(ddb, wanted)
    out = []
    for meter_id in wanted:
        row = found.get(meter_id)
        if row is None:
            out.append({
                "meter_id": meter_id,
                "thing_name": None,
                "fw_version": None,
                "site": None,
            })
            continue
        thing = prefer_thing(
            telemetry.get(meter_id),
            row.get("prov_thing"),
            row.get("link_thing"),
        )
        reported = str(row.get("reported_fw") or "").strip() or None
        prov_fw = str(row.get("prov_fw") or "").strip() or None
        out.append({
            "meter_id": meter_id,
            "thing_name": thing,
            "fw_version": reported or prov_fw,
            "site": row.get("community"),
        })
    return out


def _last_seen_things(ddb, meter_ids: list[str]) -> dict[str, str]:
    if ddb is None or not meter_ids:
        return {}
    found = {}
    for meter_id in meter_ids:
        thing = None
        for key in meter_id_keys(meter_id):
            try:
                resp = ddb.get_item(
                    TableName="meter_last_seen",
                    Key={"meterId": {"S": key}},
                    ProjectionExpression="meterId, thingName",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("meter_last_seen read failed for %s: %s", key, exc)
                continue
            item = resp.get("Item") or {}
            thing = (item.get("thingName") or {}).get("S")
            if thing:
                break
        if thing:
            found[meter_id] = thing
    return found


def artifact_for_version(images: list[dict], version: str) -> dict:
    for image in images:
        if image["version"] == version:
            return image
    raise TargetRefused(f"{version} is not in the firmware library.")


def enqueue(conn, rows: list[dict], created_by: str) -> dict:
    """Insert pending rows. Does not write onemeter_ota_site_releases."""
    cur = conn.cursor()
    batch_id = str(uuid.uuid4())
    things = [row["thing_name"] for row in rows]
    already = set()
    if things:
        cur.execute(
            """
            SELECT thing_name FROM onemeter_ota_queue
             WHERE thing_name = ANY(%s) AND status = ANY(%s)
            """,
            (things, list(OPEN_STATUSES)),
        )
        already = {row[0] for row in cur.fetchall()}
    queued = []
    skipped = []
    for row in rows:
        if row["thing_name"] in already:
            skipped.append({"thing_name": row["thing_name"], "reason": "already_queued"})
            continue
        cur.execute(
            INSERT_SQL,
            (
                batch_id,
                row["site_code"],
                row["thing_name"],
                row["meter_ids"],
                row["target_version"],
                row["artifact_key"],
                row["artifact_version_id"],
                row.get("current_fw"),
                created_by,
            ),
        )
        queued.append({
            "thing_name": row["thing_name"],
            "site_code": row["site_code"],
            "target_version": row["target_version"],
        })
    conn.commit()
    return {"batch_id": batch_id, "queued": queued, "skipped": skipped}


def queue_status(conn, batch_id: str | None = None) -> dict:
    cur = conn.cursor()
    if batch_id:
        rows = _fetch_rows(cur, "batch_id = %s", (batch_id,))
    else:
        rows = _fetch_rows(
            cur,
            "status = ANY(%s) OR (status = 'failed' AND finished_at > NOW() - INTERVAL '1 day')",
            (list(OPEN_STATUSES),),
        )
    return {
        "rows": [
            {
                "id": row["id"],
                "batch_id": row["batch_id"],
                "site_code": row["site_code"],
                "thing_name": row["thing_name"],
                "meter_ids": row["meter_ids"],
                "target_version": row["target_version"],
                "current_fw": row["current_fw"],
                "status": row["status"],
                "ota_update_id": row["ota_update_id"],
                "detail": row["detail"],
            }
            for row in rows
        ],
        "pending": sum(1 for row in rows if row["status"] == "pending"),
        "active": [
            {
                "thing_name": row["thing_name"],
                "site_code": row["site_code"],
                "target_version": row["target_version"],
            }
            for row in rows if row["status"] == "active"
        ],
        "held": sum(1 for row in rows if row["status"] == "held"),
    }


def cancel_queue(conn, iot, *, batch_id: str | None, site_code: str | None) -> dict:
    cur = conn.cursor()
    clauses = ["status = ANY(%s)"]
    params: list = [list(OPEN_STATUSES)]
    if batch_id:
        clauses.append("batch_id = %s")
        params.append(batch_id)
    if site_code:
        clauses.append("site_code = %s")
        params.append(site_code.upper())
    if not batch_id and not site_code:
        raise TargetRefused("Choose a batch or a site to cancel.")
    rows = _fetch_rows(cur, " AND ".join(clauses), tuple(params))
    canceled = []
    for row in rows:
        if row["status"] == "active" and row.get("job_id"):
            try:
                iot.cancel_job(jobId=row["job_id"], force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cancel job %s failed: %s", row["job_id"], exc)
        cur.execute(
            """
            UPDATE onemeter_ota_queue
               SET status = 'canceled', finished_at = NOW(), detail = 'canceled by operator'
             WHERE id = %s AND status = ANY(%s)
            """,
            (row["id"], list(OPEN_STATUSES)),
        )
        canceled.append(row["thing_name"])
    conn.commit()
    return {"canceled": canceled}


def resume_site(conn, site_code: str) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE onemeter_ota_queue
           SET status = 'pending', detail = NULL
         WHERE site_code = %s AND status = 'held'
        RETURNING thing_name
        """,
        (site_code.upper(),),
    )
    names = [row[0] for row in cur.fetchall()]
    conn.commit()
    return {"resumed": names}


def advance_once() -> dict:
    from customer_api import get_connection
    from meter_provisioning import _client

    with get_connection() as conn:
        return advance_ota_queue(conn, _client("iot"))


def _advance_loop() -> None:
    time.sleep(20)
    while True:
        try:
            advance_once()
        except Exception:  # noqa: BLE001
            logger.exception("OTA target advance failed")
        time.sleep(60)


def start_ota_queue_advancer() -> None:
    global _advancer_started
    if _advancer_started:
        return
    if os.environ.get("CC_OTA_TARGET_ADVANCE", "1") == "0":
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _advancer_started = True
    threading.Thread(target=_advance_loop, name="ota-target-advance", daemon=True).start()
