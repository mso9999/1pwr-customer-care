"""Unit tests for Benin MTN MoMo SMS parsing."""

import unittest
from unittest.mock import MagicMock

from momo_bj import parse_momo_bn_sms, resolve_bn_momo_account


class TestParseMomoBn(unittest.TestCase):
    def test_french_received_line(self):
        body = (
            "Vous avez recu 5000 FCFA de 22997901122. "
            "ID transaction: ABC123XYZ. Merci."
        )
        p = parse_momo_bn_sms(body)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["amount"], 5000.0)
        self.assertEqual(p["phone"], "22997901122")
        self.assertEqual(p["txn_id"], "ABC123XYZ")
        self.assertEqual(p["provider"], "momo_bj")

    def test_montant_line(self):
        body = "Montant: 10 000 FCFA de +229 97 90 11 22"
        p = parse_momo_bn_sms(body)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["amount"], 10000.0)

    def test_remark_account_in_motif(self):
        body = (
            "Montant: 2000 XOF. Motif: 0123GBO prepaid. "
            "de 22996123456"
        )
        p = parse_momo_bn_sms(body)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertIn("0123GBO", p["remark_raw"])


class TestResolveBnMomo(unittest.TestCase):
    def _mock_conn(self, account_rows: list):
        cur = MagicMock()
        results = list(account_rows)

        def fetchone_side_effect():
            if not results:
                return None
            return results.pop(0)

        cur.fetchone.side_effect = fetchone_side_effect
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn

    def test_remark_account(self):
        body = "Motif: 0456SAM electricity. Montant: 1000 FCFA. 22995111111"
        p = parse_momo_bn_sms(body)
        assert p is not None
        conn = self._mock_conn([(1,)])
        acct, alloc, _, _ = resolve_bn_momo_account(conn, body, p)
        self.assertEqual(acct, "0456SAM")
        self.assertEqual(alloc, "remark_account")


if __name__ == "__main__":
    unittest.main()
