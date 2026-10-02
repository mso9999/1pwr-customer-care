"""Safety override sends the relay open to the gateway that is publishing the meter."""

import os

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import pytest
from fastapi import HTTPException

import meter_safety_override as override


def test_open_goes_to_the_reporting_gateway(monkeypatch):
    monkeypatch.setattr(
        "meter_lifecycle.last_seen_thing_for_meter",
        lambda meter_id: "AGL-GW-0002",
    )
    assert override._gateway_thing_for_meter("000023021799") == "AGL-GW-0002"


def test_open_is_refused_when_the_meter_has_not_reported(monkeypatch):
    monkeypatch.setattr(
        "meter_lifecycle.last_seen_thing_for_meter",
        lambda meter_id: None,
    )
    with pytest.raises(HTTPException) as ctx:
        override._gateway_thing_for_meter("000023021799")
    assert ctx.value.status_code == 409
    assert "not reporting through a gateway" in ctx.value.detail
