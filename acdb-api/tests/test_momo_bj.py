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


REAL_MERCHANT_SMS = (
    "Paiement 10F de SEDJRO CONFORT BERNARD POSSY-BERRY QUENUM (2290197313991) "
    "2026-09-27 18:43:37 Message:0001KOT Solde:10563519F ID:12985"
)


class TestRealMerchantTemplate(unittest.TestCase):
    """Production MTN merchant SMS (gateway id 1893, 2026-09-27) that was left unparsed."""

    def test_amount_bare_f_suffix(self):
        p = parse_momo_bn_sms(REAL_MERCHANT_SMS)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p["amount"], 10.0)

    def test_balance_not_read_as_amount(self):
        body = "Solde: 10 563 519 FCFA. Paiement 2 500F de X (22997000000) Message:0002KOT ID:77"
        p = parse_momo_bn_sms(body)
        assert p is not None
        self.assertEqual(p["amount"], 2500.0)

    def test_fields(self):
        p = parse_momo_bn_sms(REAL_MERCHANT_SMS)
        assert p is not None
        self.assertEqual(p["txn_id"], "12985")
        self.assertEqual(p["phone"], "2290197313991")
        self.assertEqual(p["remark_raw"], "0001KOT")

    def test_bare_f_needs_payment_keyword(self):
        self.assertIsNone(parse_momo_bn_sms("Celtiis: 700 F recu. Code 0007KOT"))
        self.assertIsNone(parse_momo_bn_sms("Svp 500F pour 0007KOT merci"))

    def test_thousands_grouping(self):
        for body, expected in (
            ("Paiement 10 000F de X Message:0001KOT ID:1", 10000.0),
            ("Paiement 10.000F de X Message:0001KOT ID:1", 10000.0),
            ("Vous avez recu 1,500 FCFA de 22997901122", 1500.0),
        ):
            p = parse_momo_bn_sms(body)
            assert p is not None, body
            self.assertEqual(p["amount"], expected, body)

    def test_resolves_account_from_message(self):
        p = parse_momo_bn_sms(REAL_MERCHANT_SMS)
        assert p is not None
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (1,)
        conn.cursor.return_value = cur
        acct, alloc, _, _ = resolve_bn_momo_account(conn, REAL_MERCHANT_SMS, p)
        self.assertEqual((acct, alloc), ("0001KOT", "remark_account"))


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
