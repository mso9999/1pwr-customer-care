"""Commission execute accepts numeric GPS (Postgres / UGP JSON numbers)."""
import os

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from commission import CommissionRequest  # noqa: E402


def _base(**overrides):
    payload = {
        "account_number": "0241MAS",
        "site_code": "MAS",
        "customer_type": "HH1",
        "connection_date": "2026-09-09",
        "service_phase": "Single",
        "ampacity": "Standard",
        "national_id": "101195278426",
        "phone_number": "57410911",
        "survey_id": "MAS 1008 HH",
        "customer_signature": "x" * 40,
    }
    payload.update(overrides)
    return CommissionRequest.model_validate(payload)


def test_gps_numbers_coerce_to_strings():
    req = _base(gps_lat=28.82068, gps_lng=-29.663577)
    assert req.gps_lat == "28.82068"
    assert req.gps_lng == "-29.663577"


def test_gps_strings_pass_through():
    req = _base(gps_lat="28.82068", gps_lng="-29.663577")
    assert req.gps_lat == "28.82068"
    assert req.gps_lng == "-29.663577"


def test_gps_omitted_stays_none():
    req = _base()
    assert req.gps_lat is None
    assert req.gps_lng is None
