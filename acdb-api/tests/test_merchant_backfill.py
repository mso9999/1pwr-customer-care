import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

from merchant_export_parser import NormalizedPayment  # noqa: E402

import backfill_merchant_payments_from_exports as backfill  # noqa: E402


class MerchantBackfillTests(unittest.TestCase):
    def test_process_payments_marks_unmatched_without_account(self):
        payment = NormalizedPayment(
            external_id="ABC123",
            amount=501.0,
            currency="LSL",
            paid_at=datetime(2025, 3, 15, tzinfo=timezone.utc),
            payer_phone="26658881234",
            details_text="Customer Pay Bill Online 0252SHG",
            merchant_account_key="mpesa:test:SHG:sample",
            source_file="sample.csv",
            source_row=2,
            provider="mpesa",
            site_hint="SHG",
            account_number=None,
            resolution_method="unmatched",
        )
        conn = MagicMock()
        rows = backfill.process_payments(conn, [payment], apply=False)
        self.assertEqual(rows[0]["outcome"], "unmatched_account")

    @patch.object(backfill, "_insert_payment")
    @patch.object(backfill, "_fuzzy_already_credited", return_value=False)
    @patch.object(backfill, "_ref_in_inbound_log", return_value=False)
    @patch.object(backfill, "_conflict_for_reference", return_value=None)
    def test_process_payments_would_insert_resolved_payment(
        self,
        _conflict,
        _inbound,
        _fuzzy,
        insert_payment,
    ):
        insert_payment.return_value = {
            "outcome": "would_insert",
            "category": "connection_fee",
            "account_number": "0252SHG",
            "amount": 501.0,
            "external_id": "ABC123",
            "paid_at": "2025-03-15T10:22:00+00:00",
            "resolution_method": "remark_account",
            "source_file": "sample.csv",
            "source_row": 2,
        }
        payment = NormalizedPayment(
            external_id="",
            amount=501.0,
            currency="LSL",
            paid_at=datetime(2025, 3, 15, 10, 22, tzinfo=timezone.utc),
            payer_phone="26658881234",
            details_text="Customer Pay Bill Online 0252SHG",
            merchant_account_key="mpesa:test:SHG:sample",
            source_file="sample.csv",
            source_row=2,
            provider="mpesa",
            site_hint="SHG",
            account_number="0252SHG",
            resolution_method="remark_account",
        )
        rows = backfill.process_payments(MagicMock(), [payment], apply=False)
        self.assertEqual(rows[0]["outcome"], "would_insert")
        self.assertEqual(rows[0]["category"], "connection_fee")


    def test_validate_after_apply_counts(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.side_effect = [(5,), (2,)]
        result = backfill.validate_after_apply(conn)
        self.assertEqual(
            result,
            {"merchant_export_transactions": 5, "auto_verified_fees": 2},
        )


if __name__ == "__main__":
    unittest.main()
