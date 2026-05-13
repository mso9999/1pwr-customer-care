"""Lightweight tests for operator paid-customers XLSX backfill helpers."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "scripts" / "ops"))

import backfill_operator_paid_customers_xlsx as op  # noqa: E402


class OperatorPaidXlsxHelpersTest(unittest.TestCase):
    def test_normalize_account(self):
        self.assertEqual(op._normalize_account(" 0082mak "), "0082MAK")
        self.assertEqual(op._normalize_account(1234), "1234")

    def test_norm_ref_strips_space(self):
        self.assertEqual(op._norm_ref(" ABC  123 "), "ABC123")

    def test_parse_amount(self):
        self.assertAlmostEqual(op._parse_amount("501.00") or 0, 501.0)
        self.assertIsNone(op._parse_amount(""))
        self.assertAlmostEqual(op._parse_amount(499.0) or 0, 499.0)

    def test_source_table_tag_length(self):
        slot = op.OperatorFeeSlot(
            account="0252SHG",
            fee_type="connection_fee",
            amount=501.0,
            paid_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            reference="X" * 40,
            workbook_row=99,
        )
        tag = op._source_table_tag(slot)
        self.assertLessEqual(len(tag), 50)

    def test_process_fee_slot_skips_unknown_account(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = None
        slot = op.OperatorFeeSlot(
            account="ZZ99NOSUCH",
            fee_type="connection_fee",
            amount=501.0,
            paid_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            reference="R1",
            workbook_row=3,
        )
        row = op.process_fee_slot(conn, slot, apply=False, xlsx_name="t.xlsx")
        self.assertEqual(row["outcome"], "skipped")
        self.assertEqual(row["reason"], "unknown_account")


if __name__ == "__main__":
    unittest.main()
