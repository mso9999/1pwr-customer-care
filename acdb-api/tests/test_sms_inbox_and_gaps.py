"""SMS inbox gate, fee threshold, Benin replay block, meter clock, ledger create."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import customer_api  # noqa: F401
import crud
import fee_classifier
import ingest
import sms_formats
from fastapi import BackgroundTasks, HTTPException
from models import CCRole, CurrentUser, RecordCreateRequest, UserType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_schema_drift  # noqa: E402


def _user(roles, actions=()):
    return CurrentUser(
        user_type=UserType.employee,
        user_id="e1",
        role=roles[0],
        roles=list(roles),
        privilege_actions=list(actions),
    )


class TestInboxGate(unittest.TestCase):
    def test_operator_can_read_inbox_but_not_replay_settings(self):
        user = _user([CCRole.generic.value], actions=["operate_customer_care"])
        self.assertEqual(sms_formats.require_sms_inbox_reader(user).user_id, "e1")
        with self.assertRaises(HTTPException) as ctx:
            sms_formats.require_sms_format_editor(user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_finance_cannot_read_inbox(self):
        with self.assertRaises(HTTPException) as ctx:
            sms_formats.require_sms_inbox_reader(_user([CCRole.finance_team.value]))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_editor_can_read_inbox(self):
        self.assertEqual(
            sms_formats.require_sms_inbox_reader(_user([CCRole.onm_team.value])).user_id,
            "e1",
        )


class TestBeninReplay(unittest.TestCase):
    def test_unset_env_skips_without_crediting(self):
        body = sms_formats.ReplayIn(log_ids=[7, 8])
        env = os.environ.copy()
        env.pop("SMS_BN_REPLAY_CREDIT_ENABLED", None)
        with patch.object(sms_formats, "COUNTRY") as country, patch.dict(os.environ, env, clear=True), \
                patch("ingest._sms_incoming_process_raw") as process:
            os.environ["CC_JWT_SECRET"] = "unit-test-secret"
            country.code = "BN"
            out = sms_formats.replay_unprocessed(body, BackgroundTasks(), _user([CCRole.onm_team.value]))
        process.assert_not_called()
        self.assertEqual([row["status"] for row in out["results"]], ["skipped", "skipped"])


class TestFeeThreshold(unittest.TestCase):
    def _fees(self, threshold):
        return {
            "connection_fee_amount": 501,
            "readyboard_fee_amount": 0,
            "currency": "LSL",
            "connection_fee_threshold": threshold,
        }

    def test_unset_keeps_exact_match(self):
        fees = self._fees(None)
        with patch("fee_classifier._has_verified_fee", return_value=False), \
                patch("fee_classifier._account_fee_threshold_exempt", return_value=False):
            exact = fee_classifier.classify_payment(MagicMock(), "0001MAK", 501, fees=fees)
            other = fee_classifier.classify_payment(MagicMock(), "0001MAK", 100, fees=fees)
        self.assertEqual(exact["category"], "connection_fee")
        self.assertEqual(other["category"], "electricity")

    def test_threshold_classifies_at_or_above_unless_exempt(self):
        fees = self._fees(5000)
        with patch("fee_classifier._has_verified_fee", return_value=False), \
                patch("fee_classifier._account_fee_threshold_exempt", return_value=False):
            hit = fee_classifier.classify_payment(MagicMock(), "0001KOT", 5000, fees=fees)
            below = fee_classifier.classify_payment(MagicMock(), "0001KOT", 100, fees=fees)
        self.assertEqual(hit["category"], "connection_fee")
        self.assertEqual(below["category"], "electricity")
        with patch("fee_classifier._has_verified_fee", return_value=False), \
                patch("fee_classifier._account_fee_threshold_exempt", return_value=True):
            skipped = fee_classifier.classify_payment(MagicMock(), "0001KOT", 6000, fees=fees)
            exact = fee_classifier.classify_payment(MagicMock(), "0001KOT", 501, fees=fees)
        self.assertEqual(skipped["category"], "electricity")
        self.assertEqual(exact["category"], "connection_fee")


class TestMeterClock(unittest.TestCase):
    def test_sample_time_defaults_to_utc_plus_2(self):
        with patch.dict(os.environ, {"METER_CLOCK_OFFSET_HOURS": "2"}):
            local = datetime.strptime("202609271637", "%Y%m%d%H%M").replace(tzinfo=ingest.meter_sample_tz())
        utc = local.astimezone(timezone.utc)
        self.assertEqual(local.utcoffset(), timedelta(hours=2))
        self.assertEqual((utc.hour, utc.minute), (14, 37))


class TestLedgerCreate(unittest.TestCase):
    def test_finance_cannot_create_raw_transaction(self):
        with self.assertRaises(HTTPException) as ctx:
            crud.create_record(
                "transactions",
                RecordCreateRequest(data={"account_number": "0001KOT"}),
                BackgroundTasks(),
                _user([CCRole.finance_team.value]).model_copy(update={"permissions": {"write_transactions": True}}),
            )
        self.assertEqual(ctx.exception.status_code, 403)


class TestSchemaDriftAllowlist(unittest.TestCase):
    def test_historical_objects_are_not_new_drift(self):
        allow = check_schema_drift.load_allowlist()
        self.assertIn("onepower_bj table:wa_tickets", allow)
        self.assertEqual(
            check_schema_drift.unexpected(
                ["onepower_bj table:wa_tickets", "onepower_zm column:accounts.new_col"],
                allow,
            ),
            ["onepower_zm column:accounts.new_col"],
        )


if __name__ == "__main__":
    unittest.main()
