"""Payment/reconnect after the dummy-load relay has already opened."""

import os
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

sys_relay = types.SimpleNamespace(
    queue_validation_relay=lambda *a, **k: "reconnect-cmd",
    relay_firmware_block=lambda *a, **k: None,
)
import sys
sys.modules.setdefault("relay_control", sys_relay)

import onemeter_validation as ov  # noqa: E402


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeDdb:
    def __init__(self, item):
        self.item = item

    def get_item(self, **kwargs):
        return {"Item": self.item}


def _item(relay="0", minutes_ago=45):
    return {
        "meterId": {"S": "000023021727"},
        "thingName": {"S": "KOT-GW-0004"},
        "EnergyActive": {"S": "0.1600 kWh"},
        "Relay": {"S": relay},
        "last_seen": {"S": _iso(minutes_ago)},
        "lastAcceptedTime": {"S": "202609301600"},
    }


class PaymentAfterOpenTests(unittest.TestCase):
    def test_stale_reading_is_kept_after_disconnect(self):
        with mock.patch.object(ov, "_ddb", return_value=FakeDdb(_item())):
            state = ov._read_meter_state("000023021727", require_fresh=False)
        self.assertTrue(state["stale"])
        self.assertEqual(state["relay"], "0")

    def test_fresh_required_still_refuses_stale_before_disconnect(self):
        with mock.patch.object(ov, "_ddb", return_value=FakeDdb(_item())):
            with self.assertRaises(ov.HTTPException) as ctx:
                ov._read_meter_state("000023021727")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_payment_ready_when_ack_is_missing_but_relay_is_open(self):
        session = {"disconnect_cmd_id": "d1", "reconnect_cmd_id": None, "status": "disconnected"}
        self.assertIsNone(ov._payment_block(session, {"status": "published"}, {"relay": "0"}))

    def test_payment_ready_after_open_has_settled_without_ack(self):
        session = {"disconnect_cmd_id": "d1", "reconnect_cmd_id": None, "status": "disconnected"}
        published = datetime.now(timezone.utc) - timedelta(seconds=30)
        disconnect = {"status": "published", "relay_after": None, "published_at": published}
        self.assertIsNone(ov._payment_block(session, disconnect, {"relay": "1", "stale": False}))

    def test_payment_waits_while_the_open_was_just_queued(self):
        session = {"disconnect_cmd_id": "d1", "reconnect_cmd_id": None, "status": "disconnected"}
        disconnect = {"status": "queued", "relay_after": None, "published_at": None}
        self.assertIsNotNone(ov._payment_block(session, disconnect, {"relay": "1"}))

    def test_payment_blocked_before_disconnect(self):
        session = {"disconnect_cmd_id": None, "reconnect_cmd_id": None, "status": "load_seen"}
        self.assertIsNotNone(ov._payment_block(session, None, {"relay": "1"}))

    def test_payment_blocked_after_reconnect(self):
        session = {"disconnect_cmd_id": "d1", "reconnect_cmd_id": "r1", "status": "reconnected"}
        self.assertEqual(
            ov._payment_block(session, {"relay_after": "0"}, {"relay": "0"}),
            "A synthetic payment was already applied.",
        )


if __name__ == "__main__":
    unittest.main()
