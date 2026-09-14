"""Fleet live must list every meter reporting through a gateway.

A 1Meter PCB talks to up to 8 DDS8888 meters on one RS-485 bus.
``meter_last_seen`` is keyed by meter serial (``meterId``) with ``thingName``
as an attribute. The old Fleet-live assembler keyed that scan by Thing name
and kept only the last serial — hiding every other meter on the string.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_provisioning as mp


def ddb_item(meter_id, thing, accepted=None, seen=None, power=None, fw=None):
    item = {
        "meterId": {"S": meter_id},
        "thingName": {"S": thing},
    }
    if accepted:
        item["lastAcceptedTime"] = {"S": accepted}
    if seen:
        item["last_seen"] = {"S": seen}
    if power is not None:
        item["Power"] = {"N": str(power)}
    if fw:
        item["FirmwareVersion"] = {"S": fw}
    return item


class TestGroupLastSeenByThing(unittest.TestCase):
    def test_keeps_every_serial_on_one_gateway(self):
        items = [
            ddb_item("23022628", "SIN-GW-0001", "202609141200", "202609141201"),
            ddb_item("23022696", "SIN-GW-0001", "202609141158", "202609141159"),
            ddb_item("23022673", "SIN-GW-0002", "202609141100"),
        ]
        by = mp._group_last_seen_by_thing(items)
        self.assertEqual(len(by["SIN-GW-0001"]), 2)
        self.assertEqual(len(by["SIN-GW-0002"]), 1)
        self.assertEqual(
            [m["meter_id"] for m in by["SIN-GW-0001"]],
            ["23022628", "23022696"],
        )

    def test_skips_rows_with_no_thing(self):
        by = mp._group_last_seen_by_thing([
            {"meterId": {"S": "23022628"}},
            ddb_item("23022696", "SIN-GW-0001", "202609141158"),
        ])
        self.assertEqual(list(by), ["SIN-GW-0001"])
        self.assertEqual(by["SIN-GW-0001"][0]["meter_id"], "23022696")


class TestAssembleFleetLiveUnits(unittest.TestCase):
    def test_one_row_per_gateway_with_all_meters(self):
        connected = {
            "SIN-GW-0001": {
                "connected": True,
                "connect_ts": 1,
                "site": "SIN",
                "disconnect_reason": None,
            }
        }
        meters_by_thing = {
            "SIN-GW-0001": [
                {"meter_id": "23022628", "last_accepted": "202609141200", "last_seen": "202609141201"},
                {"meter_id": "23022696", "last_accepted": "202609141158", "last_seen": "202609141159"},
            ]
        }
        telemetry = {
            "23022628": {"latest_sample": "202609141200", "power": "0", "fw": "1.1.69"},
            "23022696": {"latest_sample": "202609141158", "power": "12", "fw": "1.1.69"},
        }
        rows = mp._assemble_fleet_live_units(connected, meters_by_thing, telemetry)
        self.assertEqual(len(rows), 1)
        unit = rows[0]
        self.assertEqual(unit["thing_name"], "SIN-GW-0001")
        self.assertEqual(unit["meter_count"], 2)
        self.assertEqual(unit["meter_ids"], ["23022628", "23022696"])
        self.assertEqual(unit["meter_id"], "23022628")
        self.assertEqual(unit["meters"][1]["power"], "12")
        self.assertEqual(unit["fw"], "1.1.69")
        self.assertTrue(unit["operational"])

    def test_gateway_without_meters_still_listed(self):
        connected = {"KOT-GW-0003": {"connected": False, "site": "KOT"}}
        rows = mp._assemble_fleet_live_units(connected, {}, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["meter_count"], 0)
        self.assertEqual(rows[0]["meter_ids"], [])
        self.assertIsNone(rows[0]["meter_id"])
        self.assertFalse(rows[0]["operational"])

    def test_last_seen_fallback_when_1meter_data_missing(self):
        meters_by_thing = {
            "SAM-GW-0001": [
                {"meter_id": "23024497", "last_accepted": "202609140900", "last_seen": "202609140901"},
            ]
        }
        rows = mp._assemble_fleet_live_units({}, meters_by_thing, {})
        self.assertEqual(rows[0]["meters"][0]["latest_sample"], "202609140900")
        self.assertTrue(rows[0]["operational"])


class TestFleetLiveEndpoint(unittest.TestCase):
    def test_queries_1meter_data_for_every_serial_on_a_gateway(self):
        ddb = MagicMock()
        iot = MagicMock()
        ddb.scan.return_value = {
            "Items": [
                ddb_item("23022628", "SIN-GW-0001", "202609141200"),
                ddb_item("23022696", "SIN-GW-0001", "202609141158"),
            ]
        }
        ddb.query.return_value = {"Items": []}

        def client(name):
            return {"dynamodb": ddb, "iot": iot}[name]

        with (
            patch.object(mp, "_client", side_effect=client),
            patch.object(
                mp,
                "_iot_search_index_all",
                return_value=[{
                    "thingName": "SIN-GW-0001",
                    "connectivity": {"connected": True, "timestamp": 1},
                    "attributes": {"site": "SIN"},
                }],
            ),
        ):
            result = mp.fleet_live(_user=MagicMock())

        queried = [
            call.kwargs["ExpressionAttributeValues"][":m"]["S"]
            for call in ddb.query.call_args_list
        ]
        self.assertEqual(set(queried), {"23022628", "23022696"})
        self.assertEqual(result["total_things"], 1)
        self.assertEqual(result["total_meters"], 2)
        self.assertEqual(result["units"][0]["meter_ids"], ["23022628", "23022696"])


if __name__ == "__main__":
    unittest.main()
