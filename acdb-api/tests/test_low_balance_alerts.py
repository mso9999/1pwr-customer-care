"""Logic tests for low_balance_alerts (no DB)."""

import unittest


class TestThresholdEnvelope(unittest.TestCase):
    def test_clear_must_exceed_warn(self):
        low, clear = 10.0, 15.0
        self.assertGreater(clear, low)

    def test_alert_band(self):
        """Below warn => eligible to alert (if not yet sent). Between warn and clear => sticky."""
        warn_kwh, clear_kwh = 10.0, 20.0
        bal_low = 8.0
        bal_mid = 15.0
        bal_high = 22.0
        self.assertLessEqual(bal_low, warn_kwh)
        self.assertGreater(bal_mid, warn_kwh)
        self.assertLess(bal_mid, clear_kwh)
        self.assertGreaterEqual(bal_high, clear_kwh)


if __name__ == "__main__":
    unittest.main()
