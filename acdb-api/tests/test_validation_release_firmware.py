"""Batch validation starts when the test gateway runs the site release firmware.

KOT-GW-0004 was USB-reflashed to 1.1.71 after a factory OTA to 1.1.61, so its
provisioning row still described the old OTA and validation refused to start.
"""

import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from fastapi import HTTPException

# relay_control and customer_api import each other; the app breaks the cycle by
# loading customer_api first. Stub the relay queue so this module loads alone.
sys.modules.setdefault(
    "relay_control", types.SimpleNamespace(
        queue_validation_relay=lambda *a, **k: None,
        relay_firmware_block=lambda *a, **k: None,
    )
)

import onemeter_validation as ov  # noqa: E402
import meter_provisioning as mp  # noqa: E402


class RequireReleaseFirmwareTests(unittest.TestCase):
    def check(self, live, target="1.1.71", ota_status="SUCCEEDED", fw=None, ota_target="1.1.61"):
        with mock.patch.object(ov, "_live_gateway_firmware", return_value=live), \
             mock.patch.object(mp, "_ota_release", return_value={"target_firmware_version": target}):
            ov._require_release_firmware("KOT-GW-0004", "000023021769", "KOT", ota_status, fw, ota_target)

    def test_usb_flashed_to_release_passes_despite_old_ota_record(self):
        self.check(live="1.1.71")

    def test_live_firmware_behind_release_names_both_versions(self):
        with self.assertRaises(HTTPException) as ctx:
            self.check(live="1.1.61")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("1.1.61", ctx.exception.detail)
        self.assertIn("1.1.71", ctx.exception.detail)

    def test_without_live_firmware_the_ota_record_must_match(self):
        with self.assertRaises(HTTPException):
            self.check(live=None, fw=None, ota_target="1.1.61")
        self.check(live=None, fw="1.1.71", ota_target="1.1.71")

    def test_without_live_firmware_ota_must_have_succeeded(self):
        with self.assertRaises(HTTPException):
            self.check(live=None, ota_status="IN_PROGRESS", fw="1.1.71", ota_target="1.1.71")


if __name__ == "__main__":
    unittest.main()
