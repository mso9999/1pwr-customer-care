import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from merchant_export_parser import (  # noqa: E402
    build_merchant_manifest,
    parse_merchant_export_file,
)


FIXTURES = ROOT.parent / "scripts" / "ops" / "fixtures" / "merchant_exports"


class MerchantExportParserTests(unittest.TestCase):
    def test_parse_mpesa_csv_filters_withdrawals(self):
        path = FIXTURES / "mpesa_merchant_sample.csv"
        payments = parse_merchant_export_file(
            path,
            merchant_account_key="mpesa:test:SHG:sample",
            provider="mpesa",
            site_hint="SHG",
        )
        self.assertEqual(len(payments), 4)
        amounts = sorted(p.amount for p in payments)
        self.assertEqual(amounts, [50.0, 100.0, 499.0, 501.0])
        self.assertTrue(all(p.external_id for p in payments))
        self.assertEqual(payments[0].paid_at.tzinfo, timezone.utc)

    def test_parse_ecocash_csv_filters_debits(self):
        path = FIXTURES / "ecocash_csv_sample.csv"
        payments = parse_merchant_export_file(
            path,
            merchant_account_key="ecocash:test:MAT:sample",
            provider="ecocash",
            site_hint="MAT",
        )
        self.assertEqual(len(payments), 2)
        self.assertEqual({p.amount for p in payments}, {25.0, 501.0})

    def test_parse_finance_reconciliation_csv(self):
        path = FIXTURES / "hq_reconciliation_sample.csv"
        payments = parse_merchant_export_file(
            path,
            merchant_account_key="mpesa:test:HQ:sample",
            provider="mpesa",
            site_hint="HQ",
        )
        self.assertEqual(len(payments), 2)
        self.assertEqual({p.amount for p in payments}, {499.0, 501.0})
        self.assertTrue(all("0171MAT" in p.details_text for p in payments))
        self.assertTrue(all(p.payer_phone.endswith("6859958") for p in payments))

    def test_parse_mpesa_xls_merchant_export(self):
        path = FIXTURES / "mpesa_merchant_sample.xls"
        payments = parse_merchant_export_file(
            path,
            merchant_account_key="mpesa:test:SMP:sample",
            provider="mpesa",
            site_hint="SMP",
        )
        self.assertEqual(len(payments), 11)
        amounts = sorted(p.amount for p in payments)
        self.assertEqual(amounts, [1.0, 5.0, 15.0, 20.0, 20.0, 20.0, 28.0, 50.0, 50.0, 70.0, 100.0])
        self.assertTrue(all(p.external_id for p in payments))
        self.assertTrue(all(p.payer_phone for p in payments))
        self.assertEqual(payments[0].paid_at.year, 2019)
        self.assertEqual(payments[0].paid_at.tzinfo, timezone.utc)

    def test_manifest_includes_fixture_files(self):
        manifest = build_merchant_manifest(FIXTURES)
        self.assertGreaterEqual(len(manifest), 2)
        providers = {item.provider for item in manifest}
        self.assertIn("mpesa", providers)
        self.assertIn("ecocash", providers)


if __name__ == "__main__":
    unittest.main()
