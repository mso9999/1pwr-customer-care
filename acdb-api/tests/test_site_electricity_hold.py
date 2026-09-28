"""Free supply during a site hold, and a meter that bills anyway."""

import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from site_billing_hold import (  # noqa: E402
    expected_meter_confirmation,
    expected_site_confirmation,
    hour_is_free,
    released_kwh,
    vend_decision,
    window_covers,
)


def _t(hour: int) -> datetime:
    return datetime(2026, 9, 28, hour, tzinfo=timezone.utc)


class SiteHoldRuleTests(unittest.TestCase):
    def test_confirmation_phrases(self):
        self.assertEqual(expected_site_confirmation("hold", " kot "), "HOLD KOT")
        self.assertEqual(expected_meter_confirmation("bill", "sm1"), "BILL SM1")

    def test_hours_inside_a_hold_are_free_including_after_it_ends(self):
        site = [(_t(8), _t(12))]
        self.assertTrue(hour_is_free(_t(9), site, []))
        self.assertFalse(hour_is_free(_t(12), site, []))
        self.assertFalse(hour_is_free(_t(7), site, []))

    def test_a_meter_set_to_bill_counts_only_after_the_switch(self):
        site = [(_t(8), None)]
        bill = [(_t(10), None)]
        self.assertTrue(hour_is_free(_t(9), site, bill))
        self.assertFalse(hour_is_free(_t(11), site, bill))
        self.assertTrue(window_covers(_t(11), _t(10), None))

    def test_electricity_during_a_hold_is_saved_not_vended(self):
        kwh, held, category = vend_decision(
            effectively_held=True, amount=1000, rate=160,
        )
        self.assertEqual((kwh, held, category), (0.0, 1000.0, "electricity_held"))

    def test_a_billing_meter_vends_immediately(self):
        kwh, held, category = vend_decision(
            effectively_held=False, amount=1000, rate=160,
        )
        self.assertEqual(held, None)
        self.assertEqual(category, "electricity")
        self.assertEqual(kwh, 6.25)

    def test_clearing_the_site_uses_the_saved_tariff(self):
        self.assertEqual(released_kwh(1000, 160), 6.25)
        self.assertEqual(released_kwh(1000, 0), 0.0)

    def test_auto_open_does_not_run_during_a_hold(self):
        import sys
        import types
        from unittest import mock

        if "boto3" not in sys.modules:
            sys.modules["boto3"] = types.ModuleType("boto3")
        import relay_control as rc

        conn = mock.Mock()
        with mock.patch.object(rc, "relay_auto_trigger_enabled", return_value=True), \
             mock.patch("site_billing_hold.account_effectively_held", return_value=True):
            self.assertIsNone(rc.maybe_auto_open_relay(conn, "0003KOT"))
        conn.cursor.assert_not_called()

    def test_safety_override_is_not_closed_by_the_hold(self):
        import sys
        import types
        from unittest import mock

        if "boto3" not in sys.modules:
            sys.modules["boto3"] = types.ModuleType("boto3")
        import relay_control as rc

        class _Cursor:
            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return ("M1", "off")

        conn = mock.Mock()
        conn.cursor.return_value = _Cursor()
        with mock.patch("site_billing_hold.account_effectively_held", return_value=True):
            self.assertIsNone(rc.maybe_hold_close_relay(conn, "0003KOT"))
