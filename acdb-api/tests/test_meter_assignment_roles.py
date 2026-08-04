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


if __name__ == "__main__":
    unittest.main()
