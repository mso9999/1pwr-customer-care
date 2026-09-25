"""Batch validation lists every meter on the test gateway so the loaded one can be picked.

KOT-GW-0004 has three meters; validation used the provisioned one, which had no load.
"""

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

sys.modules.setdefault(
    "relay_control", types.SimpleNamespace(queue_validation_relay=lambda *a, **k: None)
)

import onemeter_validation as ov  # noqa: E402


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeDdb:
    def __init__(self, seen, power):
        self.seen, self.power = seen, power

    def scan(self, **kwargs):
        thing = kwargs["ExpressionAttributeValues"][":t"]["S"]
        return {"Items": [i for i in self.seen if i["thingName"]["S"] == thing]}

    def query(self, **kwargs):
        mid = kwargs["ExpressionAttributeValues"][":m"]["S"]
        return {"Items": [{"Power": {"S": self.power[mid]}}] if mid in self.power else []}


class GatewayMetersTests(unittest.TestCase):
    def test_fresh_loaded_meter_first_stale_last(self):
        seen = [
            {"meterId": {"S": "000023021769"}, "thingName": {"S": "KOT-GW-0004"}, "last_seen": {"S": _iso(1)}, "Relay": {"S": "1"}},
            {"meterId": {"S": "000023021727"}, "thingName": {"S": "KOT-GW-0004"}, "last_seen": {"S": _iso(1)}, "Relay": {"S": "1"}},
            {"meterId": {"S": "000023021758"}, "thingName": {"S": "KOT-GW-0004"}, "last_seen": {"S": _iso(90)}, "Relay": {"S": "1"}},
            {"meterId": {"S": "000099999999"}, "thingName": {"S": "KOT-GW-0001"}, "last_seen": {"S": _iso(1)}},
        ]
        power = {"000023021769": "0.0 W", "000023021727": "18.5 W", "000023021758": "40.0 W"}
        with mock.patch.object(ov, "_ddb", return_value=FakeDdb(seen, power)):
            meters = ov._gateway_meters("KOT-GW-0004")
        self.assertEqual([m["meter_id"] for m in meters], ["000023021727", "000023021769", "000023021758"])
        self.assertEqual(meters[0]["power_w"], 18.5)
        self.assertFalse(meters[2]["fresh"])


if __name__ == "__main__":
    unittest.main()
