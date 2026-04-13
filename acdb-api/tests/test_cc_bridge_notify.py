"""Tests for country-aware WhatsApp bridge credentials."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cc_bridge_notify import bridge_credentials, notify_cc_bridge


class TestBridgeCredentials(unittest.TestCase):
    def test_ls_uses_default_env(self):
        with patch.dict(
            os.environ,
            {
                "CC_BRIDGE_NOTIFY_URL": "http://127.0.0.1:3847/notify",
                "CC_BRIDGE_SECRET": "sec-ls",
                "CC_BRIDGE_NOTIFY_URL_BN": "http://127.0.0.1:3848/notify",
                "CC_BRIDGE_SECRET_BN": "sec-bn",
            },
            clear=False,
        ):
            url, secret = bridge_credentials("LS")
            self.assertEqual(url, "http://127.0.0.1:3847/notify")
            self.assertEqual(secret, "sec-ls")

    def test_bn_uses_bn_specific_env(self):
        with patch.dict(
            os.environ,
            {
                "CC_BRIDGE_NOTIFY_URL": "http://127.0.0.1:3847/notify",
                "CC_BRIDGE_SECRET": "sec-ls",
                "CC_BRIDGE_NOTIFY_URL_BN": "http://127.0.0.1:3848/notify",
                "CC_BRIDGE_SECRET_BN": "sec-bn",
            },
            clear=False,
        ):
            url, secret = bridge_credentials("BN")
            self.assertEqual(url, "http://127.0.0.1:3848/notify")
            self.assertEqual(secret, "sec-bn")

    def test_bn_falls_back_to_generic_when_bn_missing(self):
        with patch.dict(
            os.environ,
            {
                "CC_BRIDGE_NOTIFY_URL": "http://127.0.0.1:3847/notify",
                "CC_BRIDGE_SECRET": "shared",
            },
            clear=False,
        ):
            url, secret = bridge_credentials("BN")
            self.assertEqual(url, "http://127.0.0.1:3847/notify")
            self.assertEqual(secret, "shared")


class TestNotifyMerge(unittest.TestCase):
    @patch("cc_bridge_notify.urllib.request.urlopen")
    def test_payload_includes_country_code(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.status = 200
        with patch.dict(
            os.environ,
            {
                "CC_BRIDGE_NOTIFY_URL": "http://127.0.0.1:9/notify",
                "CC_BRIDGE_SECRET": "x",
            },
            clear=False,
        ):
            with patch("country_config.COUNTRY", SimpleNamespace(code="LS")):
                notify_cc_bridge({"text": "hi", "source": "t"}, country_code="LS")
        args, _kwargs = mock_urlopen.call_args
        req = args[0]
        body = req.data.decode("utf-8")
        self.assertIn('"country_code": "LS"', body)
        self.assertIn('"text": "hi"', body)


if __name__ == "__main__":
    unittest.main()
