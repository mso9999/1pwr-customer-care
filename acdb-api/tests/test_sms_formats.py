"""Operator-editable SMS formats (migration 072)."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import customer_api  # noqa: F401  (assemble app so router imports resolve)
import sms_formats
from fastapi import HTTPException
from models import CCRole, CurrentUser, UserType

MTN_BJ = (
    "Paiement 10F de SEDJRO CONFORT BERNARD POSSY-BERRY QUENUM (2290197313991) "
    "2026-09-27 18:43:37 Message:0001KOT Solde:10563519F ID:12985"
)
MOOV_BJ = "Vous avez recu 5.000 FCFA de 22961000000. Ref: MV778899. Motif: 0042SAM"


def _fmt(**kw):
    row = {
        "id": kw.pop("id", 1),
        "kind": "payment",
        "provider": "test",
        "name": "test",
        "pattern_type": "template",
        "decimal_separator": ",",
        "priority": 100,
        "enabled": True,
        "samples": [],
    }
    row.update(kw)
    return sms_formats.build_format(row)


class TestTemplates(unittest.TestCase):
    def test_mtn_merchant_template(self):
        fmt = _fmt(pattern="Paiement {amount}F de {*} ({phone}) {*} Message:{account} {*} ID:{txn_id}")
        p = sms_formats.apply_format(fmt, MTN_BJ, "MTN MoMo")
        assert p is not None
        self.assertEqual(p["amount"], 10.0)
        self.assertEqual(p["account_hint"], "0001KOT")
        self.assertEqual(p["txn_id"], "12985")
        self.assertEqual(p["phone"], "2290197313991")

    def test_new_provider_with_comma_decimal_country(self):
        fmt = _fmt(provider="moov_bj",
                   pattern="recu {amount} FCFA de {phone}. Ref: {txn_id}. Motif: {account}")
        p = sms_formats.apply_format(fmt, MOOV_BJ, "MoovMoney")
        assert p is not None
        self.assertEqual(p["amount"], 5000.0)
        self.assertEqual(p["provider"], "moov_bj")
        self.assertEqual(p["account_hint"], "0042SAM")

    def test_sender_pattern_filters(self):
        fmt = _fmt(pattern="Paiement {amount}F de {*}", sender_pattern=r"^MTN")
        self.assertIsNone(sms_formats.apply_format(fmt, MTN_BJ, "+22997000000"))
        p = sms_formats.apply_format(fmt, MTN_BJ, "MTN MoMo")
        assert p is not None
        self.assertTrue(p["sender_checked"])

    def test_payment_requires_amount(self):
        with self.assertRaises(ValueError):
            _fmt(pattern="Paiement de {*} Message:{account}")

    def test_unknown_placeholder_rejected(self):
        with self.assertRaises(ValueError):
            _fmt(pattern="Paiement {amount}F {customer}")

    def test_regex_group_whitelist(self):
        with self.assertRaises(ValueError):
            _fmt(pattern_type="regex", pattern=r"(?P<amount>\d+)F (?P<evil>.+)")
        fmt = _fmt(pattern_type="regex", pattern=r"Paiement (?P<amount>\d+)F.*?ID:(?P<txn_id>\d+)")
        p = sms_formats.apply_format(fmt, MTN_BJ)
        assert p is not None
        self.assertEqual((p["amount"], p["txn_id"]), (10.0, "12985"))

    def test_balance_request(self):
        fmt = _fmt(kind="balance_request", pattern="Balance {account}")
        hit = sms_formats.apply_format(fmt, "balance 0123 mak", "+26657000000")
        assert hit is not None
        self.assertEqual(hit["kind"], "balance_request")
        self.assertEqual(hit["account_hint"], "0123 mak")

    def test_samples_checked(self):
        fmt = _fmt(pattern="Paiement {amount}F de {*}", samples=[
            {"text": MTN_BJ, "expect_amount": 10},
            {"text": MTN_BJ, "expect_amount": 99},
        ])
        results = sms_formats.check_samples(fmt)
        self.assertEqual([r["ok"] for r in results], [True, False])


class TestPipelineOrder(unittest.TestCase):
    def test_operator_format_before_builtin(self):
        fmt = _fmt(provider="override", pattern="Paiement {amount}F de {*}")
        builtin = MagicMock(return_value={"amount": 1.0, "provider": "builtin"})
        p = sms_formats.parse_payment(MTN_BJ, "MTN", builtin, formats=[fmt])
        self.assertEqual(p["provider"], "override")
        builtin.assert_not_called()

    def test_falls_back_to_builtin(self):
        builtin = MagicMock(return_value={"amount": 1.0, "provider": "builtin"})
        p = sms_formats.parse_payment("hello", "x", builtin, formats=[])
        self.assertEqual(p["provider"], "builtin")
        self.assertEqual(p["format_name"], "built-in")

    def test_broken_format_does_not_break_ingest(self):
        bad = _fmt(pattern="Paiement {amount}F")
        bad["rx"] = MagicMock()
        bad["rx"].search.side_effect = RuntimeError("boom")
        builtin = MagicMock(return_value={"amount": 1.0, "provider": "builtin"})
        p = sms_formats.parse_payment(MTN_BJ, "MTN", builtin, formats=[bad])
        self.assertEqual(p["provider"], "builtin")


class TestTrustedSenders(unittest.TestCase):
    def test_exact_and_short_code(self):
        s = {"trusted_senders": ["MTN MoMo", "199"], "trusted_senders_mode": "enforce"}
        self.assertTrue(sms_formats.sender_is_trusted(s, "mtn  momo"))
        self.assertTrue(sms_formats.sender_is_trusted(s, "199"))
        self.assertFalse(sms_formats.sender_is_trusted(s, "+22997000199"))

    def test_format_sender_pattern_counts_as_trusted(self):
        s = {"trusted_senders": [], "trusted_senders_mode": "enforce"}
        self.assertTrue(sms_formats.sender_is_trusted(s, "anything", {"sender_checked": True}))


class TestEditorGate(unittest.TestCase):
    def _user(self, roles, actions=()):
        return CurrentUser(user_type=UserType.employee, user_id="e1", role=roles[0], roles=list(roles),
                           privilege_actions=list(actions))

    def test_onm_and_superadmin_allowed(self):
        for role in (CCRole.onm_team.value, CCRole.superadmin.value):
            self.assertEqual(sms_formats.require_sms_format_editor(self._user([role])).user_id, "e1")

    def test_nexus_admin_allowed(self):
        u = self._user([CCRole.generic.value], actions=["administer_cc"])
        self.assertEqual(sms_formats.require_sms_format_editor(u).user_id, "e1")

    def test_finance_denied(self):
        with self.assertRaises(HTTPException) as ctx:
            sms_formats.require_sms_format_editor(self._user([CCRole.finance_team.value]))
        self.assertEqual(ctx.exception.status_code, 403)


class TestBalanceReply(unittest.TestCase):
    def test_render(self):
        text = sms_formats.render_balance_reply(
            "Ntlo ea {account} e salletsoe ke: M{balance_currency} ({balance_kwh} kWh).",
            {"account_number": "0123MAK", "balance_currency": 12.5, "balance_kwh": 2.0},
        )
        self.assertEqual(text, "Ntlo ea 0123MAK e salletsoe ke: M12.50 (2 kWh).")


class TestHandleBalanceRequest(unittest.TestCase):
    def _run(self, match, accounts_exist, phone_accounts):
        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = conn
        settings = {"balance_reply_template": "{account}: {balance_kwh} kWh",
                    "balance_unregistered_template": "not registered"}
        with patch.object(sms_formats, "get_connection", return_value=ctx), \
                patch.object(sms_formats, "get_settings", return_value=settings), \
                patch("mpesa_sms.account_exists", side_effect=lambda c, a: a in accounts_exist), \
                patch("payments._account_numbers_for_phone", return_value=phone_accounts), \
                patch("payments._balance_payload_for_conn",
                      side_effect=lambda c, a: {"account_number": a, "balance_kwh": 1.5, "balance_currency": 9}), \
                patch("sms_gateway_balance_rate.enforce_balance_gateway_rate_limit"), \
                patch("sms_gateway_balance_rate.record_balance_gateway_request"), \
                patch("sms_outbound.send_gateway_sms") as send:
            outcome = sms_formats.handle_balance_request(match, "+26657000000")
        return outcome, [c.args[1] for c in send.call_args_list]

    def test_by_account(self):
        out, sent = self._run({"account_hint": "0123 MAK"}, {"0123MAK"}, [])
        self.assertEqual((out, sent), ("balance_replied", ["0123MAK: 1.50 kWh"]))

    def test_by_phone_when_no_account_given(self):
        out, sent = self._run({"account_hint": ""}, set(), ["0001MAK", "0002MAK"])
        self.assertEqual(out, "balance_replied")
        self.assertEqual(len(sent), 2)

    def test_unknown_account_is_not_answered_by_phone(self):
        out, sent = self._run({"account_hint": "9999ZZZ"}, set(), ["0001MAK"])
        self.assertEqual((out, sent), ("balance_unregistered", ["not registered"]))


class TestIngestIntegration(unittest.TestCase):
    """The ingest entry point uses operator formats and the gateway key gate."""

    def test_parse_gateway_payment_uses_formats(self):
        import ingest

        fmt = _fmt(provider="celtiis_bj", pattern="Celtiis: {amount} F recu. Code {account}")
        with patch.object(sms_formats, "active_formats", return_value=[fmt]):
            p = ingest._parse_gateway_payment("Celtiis: 700 F recu. Code 0007KOT", "Celtiis")
        self.assertEqual((p["provider"], p["amount"], p["account_hint"]), ("celtiis_bj", 700.0, "0007KOT"))

    def test_gateway_key_enforce(self):
        import ingest

        req = MagicMock()
        req.headers = {"X-Gateway-Key": "wrong"}
        req.client = None
        with patch.object(ingest, "SMS_INGEST_GATEWAY_KEY_MODE", "enforce"), \
                patch.dict(os.environ, {"SMS_GATEWAY_KEY": "right"}):
            with self.assertRaises(HTTPException) as ctx:
                ingest._check_gateway_key(req)
            self.assertEqual(ctx.exception.status_code, 401)
            req.headers = {"X-Gateway-Key": "right"}
            ingest._check_gateway_key(req)

    def test_gateway_key_warn_accepts(self):
        import ingest

        req = MagicMock()
        req.headers = {}
        req.client = None
        with patch.object(ingest, "SMS_INGEST_GATEWAY_KEY_MODE", "warn"), \
                patch.dict(os.environ, {"SMS_GATEWAY_KEY": "right"}):
            ingest._check_gateway_key(req)


if __name__ == "__main__":
    unittest.main()
