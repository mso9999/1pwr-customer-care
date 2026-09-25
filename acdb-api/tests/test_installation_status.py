"""Per-site rollout summary behind the Meters-page warnings and walkthrough.

Benin installed gateways and 1Meters ahead of the activation walkthrough and
never recorded pole installs or meter assignments, so the Meters page showed
nothing. The summary must surface each of those gaps per site.
"""

import os
import unittest

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_provisioning as mp


def unit(thing, connect_ts=None, connected=False, meters=()):
    return {
        "thing_name": thing,
        "connected": connected,
        "connect_ts": connect_ts,
        "meter_count": len(meters),
        "meters": [
            {"meter_id": mid, "account_number": acct, "last_seen": "2026-09-23T17:38:54Z"}
            for mid, acct in meters
        ],
    }


class SummarizeSiteInstallationTests(unittest.TestCase):
    def summarize(self, units, provisioned, installed=(), commissioned=(), approved=()):
        rows = mp._summarize_site_installation(
            {"KOT": "Kotokoli", "SIN": "Sinende"},
            units,
            provisioned,
            set(installed),
            set(commissioned),
            set(approved),
        )
        return {r["site"]: r for r in rows}

    def test_unassigned_reporting_meters_and_missing_pole_records(self):
        rows = self.summarize(
            [
                unit("SIN-GW-0001", 1790185440248, meters=[("000023021718", None), ("000023021750", None)]),
                unit("SIN-GW-0003", 1790185426598, meters=[("000023021767", "0001SIN")]),
                unit("KOT-GW-0001", 0),
            ],
            {
                "SIN-GW-0001": {"is_test": False},
                "SIN-GW-0003": {"is_test": False},
                "KOT-GW-0001": {"is_test": False},
            },
            installed={"SIN-GW-0003"},
        )
        sin = rows["SIN"]
        self.assertEqual(sin["gateways_in_field"], 2)
        self.assertEqual(sin["gateways_not_on_pole"], ["SIN-GW-0001"])
        self.assertEqual(sin["assigned_meters"], 1)
        self.assertEqual(
            [m["meter_id"] for m in sin["unassigned_meters"]],
            ["000023021718", "000023021750"],
        )
        self.assertFalse(sin["walkthrough_complete"])
        self.assertEqual(rows["KOT"]["gateways_in_field"], 0)
        self.assertEqual(rows["KOT"]["gateways_provisioned"], 1)

    def test_test_units_are_not_field_gateways(self):
        rows = self.summarize(
            [unit("KOT-GW-0004", 1790339932993, connected=True)],
            {"KOT-GW-0004": {"is_test": True}},
        )
        self.assertEqual(rows["KOT"]["gateways_in_field"], 0)
        self.assertEqual(rows["KOT"]["gateways_not_on_pole"], [])
        self.assertEqual(rows["KOT"]["gateways_connected"], 1)

    def test_walkthrough_complete_needs_release_and_site_commissioning(self):
        rows = self.summarize([], {}, commissioned={"KOT"}, approved={"KOT", "SIN"})
        self.assertTrue(rows["KOT"]["walkthrough_complete"])
        self.assertFalse(rows["SIN"]["walkthrough_complete"])


if __name__ == "__main__":
    unittest.main()
