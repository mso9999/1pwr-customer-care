"""Validation reads the ~2 min live reading, not only the 15-min accepted one.

The ingestion gate accepts one reading per 15 min for billing and refreshes
live* fields on every report; waiting on the accepted row made each meter
take ~30 min on the KOT bench.
"""

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

sys.modules.setdefault(
    "relay_control", types.SimpleNamespace(
        queue_validation_relay=lambda *a, **k: None,
        relay_firmware_block=lambda *a, **k: None,
    )
)

import onemeter_validation as ov  # noqa: E402


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeDdb:
    def __init__(self, item):
        self.item = item

    def get_item(self, **kwargs):
        return {"Item": self.item}


def _row(**extra):
    row = {
        "meterId": {"S": "000023021727"},
        "thingName": {"S": "KOT-GW-0004"},
        "EnergyActive": {"S": "0.1600 kWh"},
        "Relay": {"S": "1"},
        "last_seen": {"S": _iso(12)},
        "lastAcceptedTime": {"S": "202609271600"},
    }
    row.update({k: {"S": v} for k, v in extra.items()})
    return row


class LiveStateTests(unittest.TestCase):
    def _read(self, item):
        with mock.patch.object(ov, "_ddb", return_value=FakeDdb(item)):
            return ov._read_meter_state("000023021727")

    def test_newer_live_reading_wins(self):
        state = self._read(_row(liveSeen=_iso(1), liveTime="202609271611",
                                liveEnergyActive="0.1700 kWh", liveRelay="0", liveThing="KOT-GW-0004"))
        self.assertAlmostEqual(state["energy_kwh"], 0.17)
        self.assertEqual(state["relay"], "0")

    def test_without_live_fields_uses_accepted_reading(self):
        state = self._read(_row())
        self.assertAlmostEqual(state["energy_kwh"], 0.16)
        self.assertEqual(state["relay"], "1")

    def test_older_live_reading_is_ignored(self):
        state = self._read(_row(liveSeen=_iso(20), liveTime="202609271552",
                                liveEnergyActive="0.1500 kWh", liveRelay="0", liveThing="KOT-GW-0004"))
        self.assertAlmostEqual(state["energy_kwh"], 0.16)

    def test_fresh_live_reading_rescues_stale_accepted_row(self):
        item = _row(liveSeen=_iso(2), liveTime="202609271611",
                    liveEnergyActive="0.1700 kWh", liveRelay="1", liveThing="KOT-GW-0004")
        item["last_seen"] = {"S": _iso(45)}
        state = self._read(item)
        self.assertAlmostEqual(state["energy_kwh"], 0.17)


if __name__ == "__main__":
    unittest.main()
