import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import country_config
# Import the assembled app first so its router imports complete in production
# order before individual modules are referenced below.
import customer_api  # noqa: F401
import ingest
import payments
import registration


def test_zambia_identity_and_commercial_defaults_fail_closed():
    zm = country_config.get_country("ZM")
    assert zm.currency == "ZMW"
    assert zm.dial_code == "260"
    assert zm.default_tariff_rate == 0
    assert zm.site_abbrev == {}
    assert zm.koios_sites == {}
    assert zm.active is False


def test_zambia_registration_waits_for_approved_site_roster():
    with (
        patch.object(registration, "COUNTRY", country_config.ZAMBIA),
        patch.object(registration, "live_known_sites", return_value=set()),
        pytest.raises(HTTPException) as exc,
    ):
        registration._validate_active_community("ABC")
    assert exc.value.status_code == 409
    assert "site codes" in str(exc.value.detail)


def test_zambia_rejects_foreign_or_unknown_site():
    with (
        patch.object(
            registration,
            "COUNTRY",
            SimpleNamespace(code="ZM", name="Zambia"),
        ),
        patch.object(registration, "live_known_sites", return_value={"LUS"}),
        pytest.raises(HTTPException) as exc,
    ):
        registration._validate_active_community("GBO")
    assert exc.value.status_code == 400


def test_zambia_payment_automation_defaults_off():
    with (
        patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("PAYMENT_AUTOMATION_ENABLED", None)
        os.environ.pop("METER_CREDIT_ENABLED", None)
        assert payments._payment_automation_enabled() is False
        assert payments._meter_credit_enabled() is False


def test_zambia_sms_parser_is_not_lesotho_fallback():
    with (
        patch.object(ingest, "COUNTRY", country_config.ZAMBIA),
        pytest.raises(HTTPException) as exc,
    ):
        ingest._parse_gateway_payment("unverified provider message")
    assert exc.value.status_code == 503
