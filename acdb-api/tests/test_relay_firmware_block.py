"""Relay commands are refused for gateways that freeze on them.

Firmware before 1.1.73 handled relay commands inside the MQTT agent callback and
deadlocked: KOT-GW-0004 (1.1.71) froze on a validation disconnect and the relay
never moved.
"""

import importlib
import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

# Other validation tests stub relay_control; load the real module.
if isinstance(sys.modules.get("relay_control"), types.SimpleNamespace):
    del sys.modules["relay_control"]
import customer_api  # noqa: E402,F401  (loads before relay_control, as in the app)
rc = importlib.import_module("relay_control")


class RelayFirmwareBlockTests(unittest.TestCase):
    def _block(self, firmware):
        with mock.patch.object(rc, "_gateway_firmware", return_value=firmware):
            return rc.relay_firmware_block("KOT-GW-0004", "000023021727")

    def test_old_firmware_is_blocked(self):
        self.assertIn("1.1.71", self._block("1.1.71"))
        self.assertIsNotNone(self._block("1.1.72"))
        self.assertIsNotNone(self._block("1.1.73"))

    def test_unknown_firmware_is_blocked(self):
        self.assertIn("unknown", self._block(None))
        self.assertIsNotNone(self._block("garbage"))

    def test_fixed_firmware_is_allowed(self):
        self.assertIsNone(self._block("1.1.74"))
        self.assertIsNone(self._block("1.1.100"))
        self.assertIsNone(self._block("1.2.0"))


if __name__ == "__main__":
    unittest.main()
