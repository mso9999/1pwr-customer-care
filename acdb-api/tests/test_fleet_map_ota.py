"""Meter-map popup reads the gateway's in-flight AWS IoT job."""

import os
from datetime import datetime, timezone

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_provisioning as mp


def _summary(job_id, status, when):
    return {
        "jobId": job_id,
        "jobExecutionSummary": {
            "status": status,
            "lastUpdatedAt": when,
            "queuedAt": when,
        },
    }


class _Iot:
    def __init__(self, summaries, details=None, version="1.1.76"):
        self.summaries = summaries
        self.details = details or {}
        self.version = version

    def list_job_executions_for_thing(self, thingName, maxResults=20):
        return {"executionSummaries": self.summaries}

    def describe_job_execution(self, jobId, thingName):
        return {"execution": {"statusDetails": {"detailsMap": self.details}}}

    def get_ota_update(self, otaUpdateId):
        return {"otaUpdateInfo": {"files": [{"fileVersion": self.version}]}}


def test_no_active_job():
    iot = _Iot([_summary("AFR_OTA-old", "SUCCEEDED", datetime(2026, 9, 1, tzinfo=timezone.utc))])
    out = mp.fleet_map_ota_for_thing(iot, "MAK-GW-0196")
    assert out == {"thing_name": "MAK-GW-0196", "in_flight": False}


def test_in_progress_percent_and_version():
    older = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 28, 19, 7, tzinfo=timezone.utc)
    iot = _Iot(
        [
            _summary("AFR_OTA-1m1171-MAK-GW-0196-old", "IN_PROGRESS", older),
            _summary("AFR_OTA-1m1176-MAK-GW-0196-20260928190702", "IN_PROGRESS", newer),
        ],
        {"percent": "42", "blocks_received": "224", "blocks_total": "599"},
    )
    out = mp.fleet_map_ota_for_thing(iot, "MAK-GW-0196")
    assert out["in_flight"] is True
    assert out["status"] == "IN_PROGRESS"
    assert out["percent"] == 42
    assert out["blocks_received"] == 224
    assert out["blocks_total"] == 599
    assert out["target_version"] == "1.1.76"
    assert out["job_id"].endswith("20260928190702")


def test_queued_is_zero_and_version_falls_back_to_the_job_id():
    class NoRecord(_Iot):
        def get_ota_update(self, otaUpdateId):
            raise RuntimeError("missing")

    iot = NoRecord([_summary("AFR_OTA-1m1176-KOT-GW-0006-20260928183152", "QUEUED", datetime(2026, 9, 28, tzinfo=timezone.utc))])
    out = mp.fleet_map_ota_for_thing(iot, "KOT-GW-0006")
    assert out["in_flight"] is True
    assert out["status"] == "QUEUED"
    assert out["percent"] == 0
    assert out["target_version"] == "1.1.76"
