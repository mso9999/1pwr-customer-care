"""Unit tests for the Odyssey Standard API helpers (no DB required).

These tests stub out :mod:`customer_api` (and its heavy import chain --
``contract_gen``, ``xhtml2pdf``, etc.) so the helper functions in
:mod:`odyssey_api` can be imported in isolation in CI / dev shells that
don't have every optional dep installed.
"""

import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

# Stub customer_api before importing odyssey_api -- we only need
# ``get_connection`` as an attribute, never actually called by the helpers
# under test.
_stub = types.ModuleType("customer_api")
def _stub_get_connection():  # pragma: no cover -- unused by helper tests
    raise RuntimeError("customer_api.get_connection should not be called in unit tests")
_stub.get_connection = _stub_get_connection  # type: ignore[attr-defined]
sys.modules.setdefault("customer_api", _stub)

from odyssey_api import (  # noqa: E402  (deliberate: stub above must precede)
    _classify_payment_type,
    _coerce_page,
    _format_meter_metric_record,
    _format_payment_record,
    _hash_token,
    _parse_iso,
    _validate_window,
    ODYSSEY_MAX_WINDOW_HOURS,
)


class IsoParsingTests(unittest.TestCase):
    def test_parses_z_suffix(self):
        dt = _parse_iso("from", "2026-04-30T00:00:00Z")
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.isoformat(), "2026-04-30T00:00:00+00:00")

    def test_parses_naive_assumes_utc(self):
        dt = _parse_iso("from", "2026-04-30T00:00:00")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_date_only(self):
        dt = _parse_iso("from", "2026-04-30")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 4)
        self.assertEqual(dt.day, 30)

    def test_missing_value(self):
        with self.assertRaises(HTTPException) as ctx:
            _parse_iso("from", "")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_bad_value(self):
        with self.assertRaises(HTTPException):
            _parse_iso("from", "not-a-date")


class WindowValidationTests(unittest.TestCase):
    def test_to_must_exceed_from(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(HTTPException) as ctx:
            _validate_window(now, now)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_within_max_window(self):
        now = datetime.now(timezone.utc)
        _validate_window(now, now + timedelta(hours=ODYSSEY_MAX_WINDOW_HOURS - 1))  # no raise

    def test_exceeds_max_window(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(HTTPException) as ctx:
            _validate_window(now, now + timedelta(hours=ODYSSEY_MAX_WINDOW_HOURS + 1))
        self.assertEqual(ctx.exception.status_code, 400)


class PageCoercionTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_coerce_page(1, 100), (1, 100))
        self.assertEqual(_coerce_page(5, 1000), (5, 1000))

    def test_bad_page(self):
        with self.assertRaises(HTTPException):
            _coerce_page(0, 100)

    def test_bad_page_size(self):
        with self.assertRaises(HTTPException):
            _coerce_page(1, 0)
        with self.assertRaises(HTTPException):
            _coerce_page(1, 1001)


class TokenHashTests(unittest.TestCase):
    def test_hash_is_deterministic(self):
        self.assertEqual(_hash_token("abc"), _hash_token("abc"))

    def test_hash_changes_with_input(self):
        self.assertNotEqual(_hash_token("abc"), _hash_token("abcd"))

    def test_hash_is_64_hex(self):
        h = _hash_token("ody_xyz")
        self.assertEqual(len(h), 64)
        int(h, 16)  # must parse as hex


class PaymentTypeBucketTests(unittest.TestCase):
    def test_mobile_money_buckets(self):
        for s in ("mpesa", "ecocash", "momo", "airtel", "sms", "sms_gateway"):
            self.assertEqual(_classify_payment_type(s), "mobile_money")

    def test_other_buckets(self):
        self.assertEqual(_classify_payment_type("portal"), "manual")
        self.assertEqual(_classify_payment_type("manual"), "manual")
        self.assertEqual(_classify_payment_type("cash"), "cash")
        self.assertEqual(_classify_payment_type("koios"), "system")
        self.assertEqual(_classify_payment_type(None), "other")
        self.assertEqual(_classify_payment_type("something_unknown"), "other")


class FormatPaymentRecordTests(unittest.TestCase):
    def test_full_record(self):
        row = {
            "txn_id": 42,
            "account_number": "0252SHG",
            "transaction_date": datetime(2026, 4, 29, 17, 58, 6, tzinfo=timezone.utc),
            "amount": 100.0,
            "kwh_value": 18.42,
            "is_payment": True,
            "source": "mpesa",
            "payment_reference": "08D4LT8BWS57",
            "sms_payer_phone": "26650123456",
            "txn_meter_id": "OLDMETER",
            "site_id": "SHG",
            "pg_customer_id": 999,
            "customer_id_legacy": "12345",
            "first_name": "Mosa",
            "middle_name": "T",
            "last_name": "Lephoto",
            "phone": "26650123456",
            "cell_phone_1": None,
            "latitude": -29.9,
            "longitude": 28.7,
            "meter_serial": "SMRSD-03-0001B57D",
        }
        out = _format_payment_record(row, currency="ZMW")
        self.assertEqual(out["external_id"], "08D4LT8BWS57")
        self.assertEqual(out["transaction_id"], 42)
        self.assertEqual(out["amount"], 100.0)
        self.assertEqual(out["currency"], "ZMW")
        self.assertEqual(out["kwh_value"], 18.42)
        self.assertEqual(out["payment_type"], "mobile_money")
        self.assertEqual(out["customer_id"], "12345")
        self.assertEqual(out["customer_name"], "Mosa T Lephoto")
        self.assertEqual(out["meter_serial"], "SMRSD-03-0001B57D")
        self.assertEqual(out["site_id"], "SHG")
        self.assertEqual(out["latitude"], -29.9)
        self.assertEqual(out["longitude"], 28.7)
        self.assertIsNone(out["agent_id"])

    def test_falls_back_to_txn_id_external(self):
        row = {
            "txn_id": 7,
            "account_number": "0001MAK",
            "transaction_date": datetime(2026, 4, 29, tzinfo=timezone.utc),
            "amount": None,
            "kwh_value": None,
            "is_payment": True,
            "source": None,
            "payment_reference": None,
            "sms_payer_phone": None,
            "txn_meter_id": None,
            "site_id": "MAK",
            "pg_customer_id": 1,
            "customer_id_legacy": None,
            "first_name": "",
            "middle_name": None,
            "last_name": "",
            "phone": None,
            "cell_phone_1": None,
            "latitude": None,
            "longitude": None,
            "meter_serial": None,
        }
        out = _format_payment_record(row, currency="LSL")
        self.assertEqual(out["external_id"], "txn-7")
        self.assertIsNone(out["customer_name"])
        # customer_id falls back to pg_customer_id when customer_id_legacy is null
        self.assertEqual(out["customer_id"], "1")
        self.assertIsNone(out["latitude"])
        self.assertIsNone(out["amount"])


class FormatMeterMetricRecordTests(unittest.TestCase):
    def test_normal_day(self):
        row = {
            "reading_day": datetime(2026, 4, 29, tzinfo=timezone.utc),
            "account_number": "0252SHG",
            "kwh_delivered": 4.13,
            "reading_count": 24,
            "last_reading": datetime(2026, 4, 29, 23, 0, tzinfo=timezone.utc),
            "meter_serial": "SMRSD-03-0001B57D",
            "site_id": "SHG",
            "pg_customer_id": 999,
            "customer_id_legacy": "12345",
            "first_name": "Mosa",
            "middle_name": None,
            "last_name": "Lephoto",
            "latitude": -29.9,
            "longitude": 28.7,
        }
        out = _format_meter_metric_record(row)
        self.assertEqual(out["external_id"], "SMRSD-03-0001B57D-2026-04-29")
        self.assertEqual(out["interval"], "P1D")
        self.assertEqual(out["kwh_delivered"], 4.13)
        self.assertEqual(out["error_type"], "normal")
        self.assertEqual(out["reading_count"], 24)
        self.assertEqual(out["customer_name"], "Mosa Lephoto")

    def test_offline_day_when_no_readings(self):
        row = {
            "reading_day": datetime(2026, 4, 29, tzinfo=timezone.utc),
            "account_number": "0252SHG",
            "kwh_delivered": 0,
            "reading_count": 0,
            "last_reading": None,
            "meter_serial": None,
            "site_id": "SHG",
            "pg_customer_id": None,
            "customer_id_legacy": None,
            "first_name": None,
            "middle_name": None,
            "last_name": None,
            "latitude": None,
            "longitude": None,
        }
        out = _format_meter_metric_record(row)
        # external_id falls back to account_number when meter_serial is missing
        self.assertEqual(out["external_id"], "0252SHG-2026-04-29")
        self.assertEqual(out["error_type"], "offline")
        self.assertEqual(out["kwh_delivered"], 0.0)
        self.assertIsNone(out["customer_name"])


if __name__ == "__main__":
    unittest.main()
