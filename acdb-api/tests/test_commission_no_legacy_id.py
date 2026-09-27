"""Commissioning persists customers created in CC (no ACDB legacy id).

BN customer created in CC had customer_id_legacy NULL; the flags UPDATE keyed on
it matched nothing and failed with "customer row not found after contract
generation" after the PDFs were made.
"""
import asyncio
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import commission  # noqa: E402
from commission import CommissionRequest  # noqa: E402


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.rowcount = 0
        self.description = []

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        self.rowcount = 1 if sql.lstrip().startswith("UPDATE customers") and params and params[-1] == 42 else 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_commission_updates_by_primary_key_when_legacy_id_missing():
    log = []
    req = CommissionRequest.model_validate({
        "account_number": "0001KOT",
        "site_code": "KOT",
        "customer_type": "HH1",
        "connection_date": "2026-09-27",
        "service_phase": "Single",
        "ampacity": "Standard",
        "national_id": "123",
        "phone_number": "0100000000",
        "customer_signature": "x" * 40,
        "survey_id": "KOT 0001 HH",
    })
    fake_ugp = SimpleNamespace(
        sync_commission_to_ugp=lambda **k: {"ugp_updated": False, "upstream_warnings": []},
        sync_meter_gps_from_ugp=lambda *a, **k: None,
    )
    customer = {"id": 42, "customer_id_legacy": None, "first_name": "Test", "last_name": "Bench"}
    user = SimpleNamespace(user_id="tester")
    contract = {"site_code": "KOT", "en_filename": "en.pdf", "so_filename": "fr.pdf"}
    with mock.patch.object(commission, "_get_connection", side_effect=lambda: FakeConn(log)), \
         mock.patch.object(commission, "_resolve_customer_for_commission", return_value=(customer, None, "0001KOT")), \
         mock.patch.object(commission, "_commission_gateway_gate", return_value=None), \
         mock.patch.object(commission, "generate_contract", return_value=contract), \
         mock.patch.object(commission, "build_download_url", return_value="https://x"), \
         mock.patch.dict("sys.modules", {"sync_ugridplan": fake_ugp}):
        result = asyncio.run(commission.execute_commission(req, user))
    updates = [(s, p) for s, p in log if s.lstrip().startswith("UPDATE customers")]
    assert updates and "WHERE id = %s" in updates[0][0] and updates[0][1][-1] == 42
    assert result["status"] == "ok"
