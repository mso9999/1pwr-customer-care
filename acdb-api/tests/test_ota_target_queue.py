"""Operator firmware targeting: library, dedupe, and one-at-a-time queue rules."""

import os
from datetime import datetime, timezone

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("CC_OTA_TARGET_ADVANCE", "0")

import ota_target as ot


def _ver(key, version_id, when, is_delete=False):
    return {
        "Key": key,
        "VersionId": version_id,
        "LastModified": when,
        "IsDeleteMarker": is_delete,
    }


def test_library_keeps_fleet_images_and_blocks_baseline():
    older = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 28, tzinfo=timezone.utc)
    images = ot.fleet_images_from_versions([
        _ver("firmware-releases/v1.1.76/Fleet1176/FeaturedFreeRTOSIoTIntegration.bin", "old", older),
        _ver("firmware-releases/v1.1.76/Fleet1176/FeaturedFreeRTOSIoTIntegration.bin", "new", newer),
        _ver("firmware-releases/v1.1.62/SIN-GW-0001/FeaturedFreeRTOSIoTIntegration.bin", "thing", newer),
        _ver("firmware-releases/v1.1.56/Fleet1156/FeaturedFreeRTOSIoTIntegration.bin", "base", newer),
        _ver("firmware-releases/v1.1.77/Fleet1177/FeaturedFreeRTOSIoTIntegration.bin", "next", newer),
        _ver("firmware-releases/v1.1.71/Fleet1171/FeaturedFreeRTOSIoTIntegration.bin", "gone", newer, is_delete=True),
    ])
    by_version = {row["version"]: row for row in images}
    assert set(by_version) == {"1.1.77", "1.1.76", "1.1.56"}
    assert by_version["1.1.76"]["artifact_version_id"] == "new"
    assert by_version["1.1.77"]["selectable"] is True
    assert by_version["1.1.56"]["selectable"] is False
    assert images[0]["version"] == "1.1.77"


def test_pin_ignores_an_older_site_row():
    pinned = ot.pin_release_to_latest(
        {
            "target_firmware_version": "1.1.71",
            "artifact_key": "firmware-releases/v1.1.71/Fleet1171/FeaturedFreeRTOSIoTIntegration.bin",
            "artifact_version_id": "old",
            "bucket": "1pwr-ota-firmware",
        },
        [
            {
                "version": "1.1.56",
                "artifact_key": "firmware-releases/v1.1.56/Fleet1156/FeaturedFreeRTOSIoTIntegration.bin",
                "artifact_version_id": "base",
                "selectable": False,
            },
            {
                "version": "1.1.71",
                "artifact_key": "firmware-releases/v1.1.71/Fleet1171/FeaturedFreeRTOSIoTIntegration.bin",
                "artifact_version_id": "old",
                "selectable": True,
            },
            {
                "version": "1.1.76",
                "artifact_key": "firmware-releases/v1.1.76/Fleet1176/FeaturedFreeRTOSIoTIntegration.bin",
                "artifact_version_id": "latest",
                "selectable": True,
            },
        ],
    )
    assert pinned["target_firmware_version"] == "1.1.76"
    assert pinned["artifact_version_id"] == "latest"
    assert pinned["bucket"] == "1pwr-ota-firmware"


def test_preview_dedupes_meters_on_one_gateway():
    preview = ot.build_preview([
        {"meter_id": "23022613", "thing_name": "MAK-GW-0196", "fw_version": "1.1.74", "site": "MAK"},
        {"meter_id": "23022614", "thing_name": "MAK-GW-0196", "fw_version": "1.1.74", "site": "MAK"},
        {"meter_id": "23020001", "thing_name": None, "fw_version": None, "site": "MAK"},
    ], "1.1.77")
    assert len(preview["gateways"]) == 1
    assert preview["gateways"][0]["meter_ids"] == ["23022613", "23022614"]
    assert preview["gateways"][0]["action"] == "update"
    assert preview["skipped"][0]["reason"] == "no_gateway"


def test_rollback_above_baseline_is_allowed_when_confirmed():
    preview = ot.build_preview([
        {"meter_id": "1", "thing_name": "KOT-GW-0006", "fw_version": "1.1.76", "site": "KOT"},
    ], "1.1.71")
    assert preview["rollback"] is True
    artifact = {
        "version": "1.1.71",
        "selectable": True,
        "artifact_key": "firmware-releases/v1.1.71/Fleet1171/FeaturedFreeRTOSIoTIntegration.bin",
        "artifact_version_id": "v71",
    }
    try:
        ot.rows_to_queue(preview, artifact, None)
        raise AssertionError("rollback without confirmation should be refused")
    except ot.TargetRefused:
        pass
    queued = ot.rows_to_queue(preview, artifact, "1.1.71")
    assert queued[0]["thing_name"] == "KOT-GW-0006"
    assert queued[0]["target_version"] == "1.1.71"


def test_baseline_firmware_is_refused():
    preview = ot.build_preview([
        {"meter_id": "1", "thing_name": "MAK-GW-0016", "fw_version": "1.1.56", "site": "MAK"},
    ], "1.1.76")
    assert preview["gateways"][0]["action"] == "too_old"
    artifact = {
        "version": "1.1.76",
        "selectable": True,
        "artifact_key": "k",
        "artifact_version_id": "v",
    }
    assert ot.rows_to_queue(preview, artifact, None) == []
    try:
        ot.rows_to_queue(preview, {**artifact, "version": "1.1.56", "selectable": False}, None)
        raise AssertionError("1.1.56 must not be selectable")
    except ot.TargetRefused:
        pass


def test_one_online_gateway_per_site_and_offline_waits():
    rows = [
        {"id": 1, "site_code": "MAK", "thing_name": "MAK-GW-0196", "status": "pending", "created_at": "1"},
        {"id": 2, "site_code": "MAK", "thing_name": "MAK-GW-0191", "status": "pending", "created_at": "2"},
        {"id": 3, "site_code": "KOT", "thing_name": "KOT-GW-0006", "status": "pending", "created_at": "1"},
    ]
    online = {"MAK-GW-0196", "MAK-GW-0191", "KOT-GW-0006"}
    starts = ot.plan_advance(rows, online, set())
    assert [row["thing_name"] for row in starts] == ["MAK-GW-0196", "KOT-GW-0006"]
    assert ot.plan_advance(rows, set(), set()) == []
    held_back = ot.plan_advance(rows, online, {"MAK"})
    assert [row["thing_name"] for row in held_back] == ["KOT-GW-0006"]
    busy = rows + [{
        "id": 9, "site_code": "MAK", "thing_name": "MAK-GW-0085", "status": "active", "created_at": "0",
    }]
    blocked = ot.plan_advance(busy, online, set())
    assert [row["thing_name"] for row in blocked] == ["KOT-GW-0006"]


def test_create_is_one_thing_and_does_not_touch_site_releases():
    kwargs = ot.operator_ota_kwargs(
        ota_id="1m-target-1-1-77-MAK-GW-0196-20260928220000",
        thing="MAK-GW-0196",
        site="MAK",
        version="1.1.77",
        bucket="1pwr-ota-firmware",
        key="firmware-releases/v1.1.77/Fleet1177/FeaturedFreeRTOSIoTIntegration.bin",
        version_id="abc",
    )
    assert kwargs["targets"] == [
        "arn:aws:iot:us-east-1:758201218523:thing/MAK-GW-0196"
    ]
    assert kwargs["targetSelection"] == "SNAPSHOT"
    assert kwargs["files"][0]["attributes"]["workflow"] == "operator_target"
    assert kwargs["files"][0]["codeSigning"]["startSigningJobParameter"]["destination"]["s3Destination"]["prefix"] == (
        "signed/operator-target/1.1.77"
    )
    blob = ot.INSERT_SQL + "".join(ot.QUEUE_TABLE_SQL)
    assert "onemeter_ota_site_releases" not in blob


def test_meter_id_lookup_includes_the_padded_dynamo_key():
    assert ot.meter_id_keys("23024464") == ["23024464", "000023024464"]
    assert ot.meter_id_keys("000023024464") == ["000023024464", "23024464"]


def test_connected_gateway_is_kicked_immediately_and_not_again_for_15_minutes():
    now = datetime(2026, 9, 29, 9, 45, tzinfo=timezone.utc)
    assert ot.should_kick(kicked_at=None, connected=True, now=now) is True
    assert ot.should_kick(kicked_at=None, connected=False, now=now) is False
    assert ot.should_kick(kicked_at=now, connected=True, now=now) is False
    later = datetime(2026, 9, 29, 10, 1, tzinfo=timezone.utc)
    assert ot.should_kick(kicked_at=now, connected=True, now=later) is True


def test_popup_shows_a_queue_row_before_an_aws_job_exists():
    blank = {"thing_name": "MAK-GW-0184", "in_flight": False}
    waiting = ot.present_map_ota(blank, {"target_version": "1.1.76", "queue_status": "pending"})
    assert waiting["in_flight"] is True
    assert waiting["phase"] == "waiting_online"
    assert waiting["target_version"] == "1.1.76"
    downloading = ot.present_map_ota(
        {"thing_name": "MAK-GW-0184", "in_flight": True, "status": "IN_PROGRESS", "percent": 12},
        {"target_version": "1.1.76", "queue_status": "active"},
    )
    assert downloading["phase"] == "downloading"
    assert downloading["percent"] == 12


def test_already_on_target_is_skipped():
    preview = ot.build_preview([
        {"meter_id": "1", "thing_name": "MAK-GW-0196", "fw_version": "1.1.76", "site": "MAK"},
    ], "1.1.76")
    assert preview["gateways"][0]["action"] == "already"


def test_library_falls_back_to_current_objects_when_versions_denied():
    ot._library_cache.clear()
    denied = Exception(
        "An error occurred (AccessDenied) when calling the ListObjectVersions "
        "operation: User is not authorized to perform s3:ListBucketVersions"
    )
    denied.response = {"Error": {"Code": "AccessDenied"}}
    newer = datetime(2026, 9, 29, tzinfo=timezone.utc)
    s3 = type("S3", (), {})()
    s3.list_object_versions = lambda **_kwargs: (_ for _ in ()).throw(denied)
    s3.list_objects_v2 = lambda **_kwargs: {
        "Contents": [
            {
                "Key": "firmware-releases/v1.1.76/Fleet1176/FeaturedFreeRTOSIoTIntegration.bin",
                "LastModified": newer,
            },
            {
                "Key": "firmware-releases/v1.1.71/SIN-GW-0001/FeaturedFreeRTOSIoTIntegration.bin",
                "LastModified": newer,
            },
        ],
        "IsTruncated": False,
    }
    s3.head_object = lambda **_kwargs: {"VersionId": "current-76"}
    images = ot.library_from_s3(s3, bucket="1pwr-ota-firmware", use_cache=False)
    assert images[0]["version"] == "1.1.76"
    assert images[0]["artifact_version_id"] == "current-76"
    assert images[0]["selectable"] is True
