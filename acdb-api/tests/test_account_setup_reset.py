"""Setup reset keeps the account and restamps fees. It refuses a mature account."""

import os
import unittest
from datetime import date

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from account_setup_reset import (  # noqa: E402
    expected_confirmation,
    reseed_fee_debt,
    setup_reset_refusal,
)


class SetupResetRuleTests(unittest.TestCase):
    def test_confirmation_is_the_account_number(self):
        self.assertEqual(expected_confirmation(" 0003kot "), "RESET 0003KOT")

    def test_readyboard_is_stamped_only_when_the_customer_takes_the_kit(self):
        self.assertEqual(
            reseed_fee_debt(10000, 30000, acquires_readyboard=True),
            {"fee_debt_connection_remaining": 10000.0, "fee_debt_readyboard_remaining": 30000.0},
        )
        self.assertEqual(
            reseed_fee_debt(10000, 30000, acquires_readyboard=False)["fee_debt_readyboard_remaining"],
            0.0,
        )

    def test_fresh_install_is_allowed(self):
        self.assertIsNone(setup_reset_refusal(
            payment_count=1,
            oldest_payment=date(2026, 9, 28),
            service_connected=date(2026, 9, 28),
            today=date(2026, 9, 28),
            has_advance=False,
            has_financing=False,
            has_unmetered=False,
        ))

    def test_old_payment_is_refused(self):
        reason = setup_reset_refusal(
            payment_count=2,
            oldest_payment=date(2026, 1, 1),
            service_connected=date(2026, 9, 28),
            today=date(2026, 9, 28),
            has_advance=False,
            has_financing=False,
            has_unmetered=False,
        )
        self.assertIn("21 days", reason or "")

    def test_active_advance_is_refused(self):
        self.assertIn("advance", setup_reset_refusal(
            payment_count=0,
            oldest_payment=None,
            service_connected=None,
            today=date(2026, 9, 28),
            has_advance=True,
            has_financing=False,
            has_unmetered=False,
        ) or "")
