"""Unit tests for the monthly staff-PIN broadcast helpers and friendly 401."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


# Stub heavy customer_api / middleware import chain so auth.py can be
# imported in isolation -- same pattern as test_odyssey_api.
def _install_stubs():
    if "customer_api" not in sys.modules:
        m = types.ModuleType("customer_api")
        m.get_connection = lambda: None  # type: ignore[attr-defined]
        m._row_to_dict = lambda *a, **k: {}  # type: ignore[attr-defined]
        m._normalize_customer = lambda d: d  # type: ignore[attr-defined]
        sys.modules["customer_api"] = m


_install_stubs()

from auth import date_password_for, generate_date_password  # noqa: E402
from auth_pin_broadcast import (  # noqa: E402
    broadcast_pin_for_active_countries,
    compose_pin_message,
    is_first_week_of_month,
)


class DatePasswordForTests(unittest.TestCase):
    """Pinned values: drift here would break logins for the whole company."""

    def test_april_2026(self):
        self.assertEqual(date_password_for(2026, 4), "4987")

    def test_may_2026(self):
        # The month that triggered the original report.
        self.assertEqual(date_password_for(2026, 5), "4002")

    def test_june_2026(self):
        self.assertEqual(date_password_for(2026, 6), "3342")

    def test_january_handles_leading_zero(self):
        # Not a regression test for any specific value -- just ensures
        # the formula doesn't crash on months with a leading zero.
        out = date_password_for(2026, 1)
        self.assertEqual(len(out), 4)
        self.assertTrue(out.isdigit())


class GenerateDatePasswordTests(unittest.TestCase):
    def test_matches_date_password_for_today(self):
        from datetime import datetime
        now = datetime.utcnow()
        self.assertEqual(generate_date_password(), date_password_for(now.year, now.month))


class ComposePinMessageTests(unittest.TestCase):
    def test_message_includes_current_pin(self):
        msg = compose_pin_message(2026, 5)
        self.assertIn("May 2026", msg)
        self.assertIn("*4002*", msg)

    def test_message_includes_next_month_pin(self):
        msg = compose_pin_message(2026, 5)
        self.assertIn("June 2026", msg)
        self.assertIn("*3342*", msg)

    def test_no_next_month_when_disabled(self):
        msg = compose_pin_message(2026, 5, include_next_month=False)
        self.assertIn("*4002*", msg)
        self.assertNotIn("June 2026", msg)
        self.assertNotIn("*3342*", msg)

    def test_december_rolls_to_january(self):
        msg = compose_pin_message(2026, 12)
        self.assertIn("December 2026", msg)
        self.assertIn("January 2027", msg)


class IsFirstWeekOfMonthTests(unittest.TestCase):
    def test_first_day(self):
        self.assertTrue(is_first_week_of_month(datetime(2026, 5, 1, tzinfo=timezone.utc)))

    def test_seventh_day(self):
        self.assertTrue(is_first_week_of_month(datetime(2026, 5, 7, tzinfo=timezone.utc)))

    def test_eighth_day(self):
        self.assertFalse(is_first_week_of_month(datetime(2026, 5, 8, tzinfo=timezone.utc)))

    def test_end_of_month(self):
        self.assertFalse(is_first_week_of_month(datetime(2026, 5, 31, tzinfo=timezone.utc)))


class BroadcastPinForActiveCountriesTests(unittest.TestCase):
    def test_skips_inactive_country_zm(self):
        """ZM is registered with active=False -- broadcast must skip it."""
        from country_config import _REGISTRY  # type: ignore[attr-defined]

        # Sanity: ZM should be in the registry but inactive.
        self.assertIn("ZM", _REGISTRY)
        self.assertFalse(_REGISTRY["ZM"].active)

        with patch("auth_pin_broadcast.broadcast_to_bridge", return_value=True) as mock_bcast:
            results = broadcast_pin_for_active_countries(
                when=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        codes = [r["country_code"] for r in results]
        self.assertNotIn("ZM", codes)
        # And the active ones must have been called.
        self.assertGreater(len(results), 0)
        self.assertEqual(mock_bcast.call_count, len(results))

    def test_only_filter(self):
        with patch("auth_pin_broadcast.broadcast_to_bridge", return_value=True):
            results = broadcast_pin_for_active_countries(
                when=datetime(2026, 5, 1, tzinfo=timezone.utc),
                only=["LS"],
            )
        codes = [r["country_code"] for r in results]
        self.assertEqual(codes, ["LS"])

    def test_failed_bridge_reported(self):
        with patch("auth_pin_broadcast.broadcast_to_bridge", return_value=False):
            results = broadcast_pin_for_active_countries(
                when=datetime(2026, 5, 1, tzinfo=timezone.utc),
                only=["LS"],
            )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])

    def test_pin_not_leaked_in_result(self):
        with patch("auth_pin_broadcast.broadcast_to_bridge", return_value=True):
            results = broadcast_pin_for_active_countries(
                when=datetime(2026, 5, 1, tzinfo=timezone.utc),
                only=["LS"],
            )
        self.assertEqual(results[0]["pin_prefix"], "4***")
        self.assertNotIn("4002", str(results[0]))


if __name__ == "__main__":
    unittest.main()
