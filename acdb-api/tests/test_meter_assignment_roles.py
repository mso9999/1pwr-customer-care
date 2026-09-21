"""Regression tests for platform selection and primary/secondary meter rules."""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from fastapi import HTTPException

import meter_lifecycle as lifecycle


def employee() -> MagicMock:
    return MagicMock(role="onm_team", user_id="operator-1")


def connection_context(fetch_rows: list[tuple | None]):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = fetch_rows
    context = MagicMock()
    context.__enter__.return_value = conn
    context.__exit__.return_value = False
    return context, conn, cursor


class TestAssignmentLabels(unittest.TestCase):
    def test_secondary_maps_to_legacy_check_without_changing_reporting_contract(self):
        self.assertEqual(lifecycle._normalise_assignment_role("secondary"), "check")
        self.assertEqual(lifecycle._operator_role("check"), "secondary")

    def test_first_meter_is_primary_and_additional_meter_is_secondary(self):
        self.assertEqual(lifecycle._assignment_role(None, False), "primary")
        self.assertEqual(lifecycle._assignment_role("existing-primary", False), "check")
        self.assertEqual(lifecycle._assignment_role("existing-primary", True), "primary")

    def test_platform_is_limited_to_routable_meter_types(self):
        self.assertEqual(lifecycle._normalise_platform("SparkMeter"), "sparkmeter")
        self.assertEqual(lifecycle._normalise_platform("prototype"), "prototype")
        with self.assertRaises(HTTPException):
            lifecycle._normalise_platform("unknown")

    def test_1meter_assignment_requires_telemetry_discovered_gateway(self):
        request = lifecycle.AssignMeterRequest(
            customer_identifier="5846",
            meter_id="23021643",
            platform="prototype",
            community="MAK",
            customer_type="HH1",
            account_number="0017MAK",
            connection_date="2026-08-04",
        )
        with self.assertRaises(HTTPException) as ctx:
            lifecycle.assign_meter(request, employee())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("provisioned 1Meter gateway", ctx.exception.detail)


class TestEditAssignment(unittest.TestCase):
    def test_duplicate_primary_can_be_corrected_to_secondary(self):
        context, conn, cursor = connection_context([
            ("0017MAK", "sparkmeter", "primary", "active"),
            ("SMRSDRF-01-0003E193",),
            ("sparkmeter",),
        ])
        with (
            patch.object(lifecycle, "_get_connection", return_value=context),
            patch.object(lifecycle, "log_mutation"),
        ):
            result = lifecycle.update_meter_assignment(
                "23021643",
                lifecycle.MeterAssignmentUpdate(
                    platform="sparkmeter",
                    role="secondary",
                    note="Correct duplicate primary",
                ),
                employee(),
            )

        self.assertEqual(result["role"], "secondary")
        self.assertEqual(result["platform"], "sparkmeter")
        self.assertTrue(any(
            "UPDATE meters SET platform" in call.args[0]
            and call.args[1] == ("sparkmeter", "check", "23021643")
            for call in cursor.execute.call_args_list
        ))
        self.assertTrue(any(
            "UPDATE accounts SET meter_id" in call.args[0]
            and call.args[1] == ("SMRSDRF-01-0003E193", "sm", "0017MAK")
            for call in cursor.execute.call_args_list
        ))
        conn.commit.assert_called_once()

    def test_only_primary_cannot_be_demoted_without_replacement(self):
        context, conn, _cursor = connection_context([
            ("0017MAK", "sparkmeter", "primary", "active"),
            None,
        ])
        with patch.object(lifecycle, "_get_connection", return_value=context):
            with self.assertRaises(HTTPException) as ctx:
                lifecycle.update_meter_assignment(
                    "only-meter",
                    lifecycle.MeterAssignmentUpdate(platform="sparkmeter", role="secondary"),
                    employee(),
                )
        self.assertEqual(ctx.exception.status_code, 409)
        conn.rollback.assert_called_once()

    def test_promoting_1meter_demotes_prior_primary_and_switches_billing_pointer(self):
        context, conn, cursor = connection_context([
            ("0017MAK", "prototype", "check", "active"),
            ("SMRSDRF-01-0003E193",),
        ])
        cursor.rowcount = 1
        with (
            patch.object(lifecycle, "_get_connection", return_value=context),
            patch.object(lifecycle, "log_mutation"),
        ):
            result = lifecycle.update_meter_assignment(
                "23021643",
                lifecycle.MeterAssignmentUpdate(platform="prototype", role="primary"),
                employee(),
            )

        self.assertEqual(result["role"], "primary")
        self.assertEqual(result["demoted_count"], 1)
        self.assertTrue(any(
            "UPDATE accounts SET meter_id" in call.args[0]
            and call.args[1] == ("23021643", "1m", "0017MAK")
            for call in cursor.execute.call_args_list
        ))
        conn.commit.assert_called_once()


class TestLockGatewayForAssignment(unittest.TestCase):
    def _gateway_cursor(self, fetch_rows):
        cursor = MagicMock()
        cursor.fetchone.side_effect = fetch_rows
        cursor.description = [
            ("thing_name",), ("meter_serial",), ("site",), ("account_number",),
            ("status",), ("last_seen_online",), ("ota_status",), ("fw_version",),
            ("ota_target_version",),
        ]
        return cursor

    def test_second_meter_on_same_gateway_can_go_to_another_account(self):
        cursor = self._gateway_cursor([
            ("SIN-GW-0001", "000023021718", "SIN", "0001SIN", "commissioned", None, None, None, None),
            None,
            None,
        ])
        with (
            patch.object(lifecycle, "last_seen_thing_for_meter", return_value="SIN-GW-0001"),
            patch("sync_ugridplan.gateway_function_state", return_value={"state": "online"}),
        ):
            meter_id, _gw = lifecycle._lock_provisioned_gateway_for_assignment(
                cursor,
                thing_name="SIN-GW-0001",
                requested_meter_id="000023021750",
                community="SIN",
                account_number="0002SIN",
            )
        self.assertEqual(meter_id, "000023021750")

    def test_rejects_meter_already_bound_to_another_account(self):
        cursor = self._gateway_cursor([
            ("SIN-GW-0003", "000023021767", "SIN", "0001SIN", "commissioned", None, "SUCCEEDED", "1.1.69", None),
            ("000023021767", "0001SIN", "active"),
        ])
        with (
            patch.object(lifecycle, "last_seen_thing_for_meter", return_value="SIN-GW-0003"),
            patch("sync_ugridplan.gateway_function_state", return_value={"state": "online"}),
        ):
            with self.assertRaises(HTTPException) as ctx:
                lifecycle._lock_provisioned_gateway_for_assignment(
                    cursor,
                    thing_name="SIN-GW-0003",
                    requested_meter_id="000023021767",
                    community="SIN",
                    account_number="0002SIN",
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("0001SIN", ctx.exception.detail)

    def test_usb_hop_without_ota_succeeded_is_allowed(self):
        cursor = self._gateway_cursor([
            ("SIN-GW-0001", "000023021718", "SIN", None, None, None, None, None, None),
            None,
            None,
        ])
        with (
            patch.object(lifecycle, "last_seen_thing_for_meter", return_value="SIN-GW-0001"),
            patch("sync_ugridplan.gateway_function_state", return_value={"state": "online"}),
        ):
            meter_id, _gw = lifecycle._lock_provisioned_gateway_for_assignment(
                cursor,
                thing_name="SIN-GW-0001",
                requested_meter_id="000023021718",
                community="SIN",
                account_number="0003SIN",
            )
        self.assertEqual(meter_id, "000023021718")


if __name__ == "__main__":
    unittest.main()
