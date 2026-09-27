"""1Meter readings enter billing on customer commissioning, not per-gateway rows.

KOT-GW-0004 reads three meters but meter_provisioning holds one serial per
Thing, and commissioning never marked that row. Every reading for 0001KOT was
refused with 409 (2026-09-27).
"""
import os
from unittest import mock

import pytest
from fastapi import HTTPException

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import customer_api  # noqa: E402,F401  (loads ingest in app order)
import ingest  # noqa: E402
from onemeter_binding import ASSIGNMENT_MISMATCH, NOT_COMMISSIONED, telemetry_refusal  # noqa: E402


class FakeCursor:
    """Answers by SQL fragment; records every statement."""

    def __init__(self, commissioned=True, conflict=False, promoted=0):
        self.commissioned = commissioned
        self.conflict = conflict
        self.promoted = promoted
        self.log = []
        self._row = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.log.append(sql)
        self._row, self.rowcount = None, 0
        if "FROM meters WHERE meter_id = ANY" in sql:
            self._row = ("000023021758", "0002KOT", "KOT")
        elif "customer_commissioned FROM accounts" in sql:
            self._row = (self.commissioned,)
        elif "FROM meter_provisioning" in sql and "UPPER(account_number) <>" in sql:
            self._row = (1,) if self.conflict else None
        elif sql.lstrip().startswith("UPDATE meter_provisioning"):
            self.rowcount = self.promoted

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reading():
    return ingest.MeterReading.model_validate({
        "meter_id": "000023021758",
        "thing_name": "KOT-GW-0004",
        "timestamp": "202609271821",
        "energy_active": 0.5,
        "power_active": 12.0,
        "relay": "1",
    })


def _post(cur):
    with mock.patch.object(ingest, "get_connection", return_value=FakeConn(cur)):
        return ingest.ingest_meter_reading(_reading(), x_iot_key=ingest.IOT_KEY)


def test_refusal_rules():
    assert telemetry_refusal(FakeCursor(commissioned=False), "23021758", "0002KOT") == NOT_COMMISSIONED
    assert telemetry_refusal(FakeCursor(conflict=True), "23021758", "0002KOT") == ASSIGNMENT_MISMATCH
    assert telemetry_refusal(FakeCursor(), "23021758", "0002KOT") is None


def test_second_meter_on_gateway_is_accepted_without_its_own_provisioning_row():
    cur = FakeCursor()
    result = _post(cur)
    assert result["status"] == "ok" and result["account"] == "0002KOT"
    assert any("INSERT INTO meter_readings" in s for s in cur.log)
    assert any(s.lstrip().startswith("UPDATE meter_provisioning") for s in cur.log)


def test_uncommissioned_customer_is_refused():
    with pytest.raises(HTTPException) as exc:
        _post(FakeCursor(commissioned=False))
    assert exc.value.status_code == 409 and exc.value.detail == NOT_COMMISSIONED


def test_serial_commissioned_to_another_account_is_refused():
    with pytest.raises(HTTPException) as exc:
        _post(FakeCursor(conflict=True))
    assert exc.value.status_code == 409 and exc.value.detail == ASSIGNMENT_MISMATCH
