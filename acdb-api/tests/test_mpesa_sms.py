"""Unit tests for M-Pesa SMS remark parsing and account token extraction."""

import unittest
from unittest.mock import MagicMock

from mpesa_sms import (
    candidate_accounts_from_text,
    extract_remark_text,
    parse_ls_sms_payment,
    parse_mpesa_sms,
    resolve_sms_account,
)

# Anonymized Lesotho-style templates (digits / codes are examples)
SAMPLE_WITH_REMARK = """
TJK9XYZ1A2 Confirmed. on 10/4/2026 at 9:00 AM
M125.50 received from 26650123456. New M-Pesa balance is M500.00.
Reference: 987654321
Remark: 0252 SHG electricity
""".strip()

SAMPLE_REMARK_COMPACT = (
    "ABC1CONF Confirmed. on 1/1/2026 at 12:00 PM "
    "M50.00 received from 26650111111. Reference: 111 "
    "Remark:0252SHG bill"
)

# EcoCash (MAT / Lesotho): same structural lines as M-Pesa but EcoCash-branded
SAMPLE_ECOCASH_MPESA_SHAPED = """
ECO1TXN2 Confirmed. on 14/4/2026 at 10:00 AM
EcoCash: M150.00 received from 26650123456. New balance M400.00.
Reference: 1122334455
Remark: 0045 MAT electricity
""".strip()

# No "Confirmed." — only EcoCash line + M received (would fail pure parse_mpesa_sms)
SAMPLE_ECOCASH_MINIMAL = (
    "EcoCash: M80.00 received from 26650999999. Ref 998877. "
    "Remark: 1234MAT"
)


class TestExtractRemark(unittest.TestCase):
    def test_remark_line_multiline(self):
        r = extract_remark_text(SAMPLE_WITH_REMARK)
        self.assertEqual(r, "0252 SHG electricity")

    def test_remark_compact(self):
        r = extract_remark_text(SAMPLE_REMARK_COMPACT)
        self.assertEqual(r, "0252SHG bill")


class TestCandidateAccounts(unittest.TestCase):
    def test_from_remark_spacing(self):
        c = candidate_accounts_from_text("0252 SHG electricity")
        self.assertEqual(c, ["0252SHG"])

    def test_order_unique(self):
        c = candidate_accounts_from_text("pay 0252 SHG and 0045 MAT")
        self.assertEqual(c, ["0252SHG", "0045MAT"])


class TestParseEcoCashLs(unittest.TestCase):
    def test_branded_same_shape_as_mpesa(self):
        p = parse_mpesa_sms(SAMPLE_ECOCASH_MPESA_SHAPED)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["provider"], "mpesa")

        p2 = parse_ls_sms_payment(SAMPLE_ECOCASH_MPESA_SHAPED, "")
        self.assertIsNotNone(p2)
        assert p2 is not None
        self.assertEqual(p2["provider"], "ecocash")
        self.assertEqual(p2["amount"], 150.0)

    def test_minimal_ecocash_line(self):
        # M-line may also match M-Pesa fallback; full gateway path still labels EcoCash
        p = parse_ls_sms_payment(SAMPLE_ECOCASH_MINIMAL, "")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["provider"], "ecocash")
        self.assertEqual(p["amount"], 80.0)

    def test_sender_199_marks_ecocash(self):
        # Same M-line as M-Pesa; short code 199 identifies EcoCash for ingest/logging
        body = "M25.00 received from 26650111111. Reference 42"
        p = parse_mpesa_sms(body)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["provider"], "mpesa")
        p2 = parse_ls_sms_payment(body, "199")
        self.assertEqual(p2["provider"], "ecocash")
        self.assertEqual(p2["amount"], 25.0)


class TestParseMpesaSms(unittest.TestCase):
    def test_full_template(self):
        p = parse_mpesa_sms(SAMPLE_WITH_REMARK)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["amount"], 125.5)
        self.assertEqual(p["phone"], "26650123456")
        self.assertEqual(p["reference"], "987654321")
        self.assertEqual(p["remark_raw"], "0252 SHG electricity")

    def test_loose_reference(self):
        p = parse_mpesa_sms(SAMPLE_REMARK_COMPACT)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["amount"], 50.0)
        self.assertEqual(p["remark_raw"], "0252SHG bill")


class TestResolveSmsAccount(unittest.TestCase):
    def _mock_conn(self, account_rows: list):
        """account_rows: fetchone results in order (account_exists, then phone_to_account)."""
        cur = MagicMock()
        results = list(account_rows)

        def execute_side_effect(sql, params=None):
            pass

        def fetchone_side_effect():
            if not results:
                return None
            return results.pop(0)

        cur.execute.side_effect = execute_side_effect
        cur.fetchone.side_effect = fetchone_side_effect
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn

    def test_remark_wins_when_account_exists(self):
        p = parse_mpesa_sms(SAMPLE_WITH_REMARK)
        assert p is not None
        conn = self._mock_conn([(1,)])
        acct, alloc, remark, reason = resolve_sms_account(conn, SAMPLE_WITH_REMARK, p)
        self.assertEqual(acct, "0252SHG")
        self.assertEqual(alloc, "remark_account")
        self.assertEqual(reason, "")

    def test_phone_fallback_when_remark_unknown(self):
        body = (
            "TXN99 Confirmed. on 1/1/2026 at 1:00 PM "
            "M10.00 received from 26650123456. Reference: 1 "
            "Remark: 9999ZZZ unknown"
        )
        p = parse_mpesa_sms(body)
        assert p is not None
        conn = self._mock_conn([None, ("0045MAT",)])
        acct, alloc, remark, reason = resolve_sms_account(conn, body, p)
        self.assertEqual(acct, "0045MAT")
        self.assertEqual(alloc, "phone_fallback")
        self.assertIn("remark_candidates_not_in_db", reason)


if __name__ == "__main__":
    unittest.main()
