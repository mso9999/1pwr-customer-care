"""Unit tests for CC-managed factory-boot -> full-firmware OTA promotion."""

import json
import types
import unittest
from unittest.mock import MagicMock, patch

import meter_provisioning as mp


def approved_release(site: str = "GBO") -> dict:
    return {
        "site_code": site,
        "region": "us-east-1",
        "account_id": "758201218523",
        "bucket": "1pwr-ota-firmware",
        "artifact_key": f"firmware/{site}/full-v1.2.3.bin",
        "artifact_version_id": "immutable-version-id",
        "target_firmware_version": "1.2.3",
        "factory_baseline_version": "1.1.56",
        "signing_profile": "1PWR_OTA_ESP32_v2",
        "role_arn": "arn:aws:iam::758201218523:role/1pwr-ota-service-role",
        "signed_prefix": "signed/factory-promotion",
        "certificate_path_on_device": "/",
        "max_per_minute": 10,
        "credentials_mode": "runtime_nvs",
        "fallback_ssid": None,
        "canary_only": False,
        "catalog_canary_only": False,
        "approval": None,
        "approved_sites": ["GBO"],
        "config_error": None,
    }


class TestOtaReleaseSelection(unittest.TestCase):
    def test_site_catalog_selects_immutable_release_without_password(self):
        catalog = {
            "GBO": {
                "artifact_key": "firmware/GBO/full.bin",
                "artifact_version_id": "s3-version-123",
                "target_firmware_version": "1.2.3",
                "fallback_ssid": "GBO-Starlink",
            }
        }
        with patch.object(mp, "OTA_RELEASES_JSON", json.dumps(catalog)):
            release = mp._ota_release("GBO")
        self.assertEqual(release["artifact_version_id"], "s3-version-123")
        self.assertEqual(release["target_firmware_version"], "1.2.3")
        self.assertEqual(release["fallback_ssid"], "GBO-Starlink")
        self.assertNotIn("password", release)

    def test_unapproved_site_fails_closed(self):
        with patch.object(
            mp,
            "OTA_RELEASES_JSON",
            json.dumps({"GBO": approved_release("GBO")}),
        ):
            release = mp._ota_release("SAM")
        self.assertIn("artifact_key", mp._ota_missing_config(release))
        self.assertEqual(release["approved_sites"], ["GBO"])

    def test_recorded_approval_unlocks_exact_candidate(self):
        catalog = {
            "GBO": {
                "artifact_key": "firmware/GBO/full.bin",
                "artifact_version_id": "s3-version-123",
                "target_firmware_version": "1.2.3",
                "factory_baseline_version": "1.1.56",
                "canary_only": True,
            }
        }
        approval = {
            "canary_ota_update_id": "ota-canary-1",
            "approved_by": "engineer",
        }
        with (
            patch.object(mp, "OTA_RELEASES_JSON", json.dumps(catalog)),
            patch.object(mp, "_release_approval", return_value=approval),
        ):
            release = mp._ota_release("GBO")
        self.assertTrue(release["catalog_canary_only"])
        self.assertFalse(release["canary_only"])
        self.assertEqual(release["approval"], approval)


class TestCanarySelfService(unittest.TestCase):
    def test_candidate_readiness_allows_first_allocation_without_allowlist(self):
        release = approved_release()
        release["canary_only"] = True
        release["catalog_canary_only"] = True
        with (
            patch.object(mp, "_ota_release", return_value=release),
            patch.object(mp, "_ota_missing_config", return_value=[]),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
            patch.object(mp, "_approved_test_things", return_value=set()),
        ):
            result = mp.ota_readiness("GBO", MagicMock())
        self.assertTrue(result["candidate_ready"])
        self.assertFalse(result["canary_ready"])
        self.assertFalse(result["ready"])

    def test_canary_registry_claim_is_persisted_as_test_unit(self):
        ddb = MagicMock()
        with (
            patch.object(mp, "_client", return_value=ddb),
            patch.object(mp, "_registry_get_by_thing", return_value=[]),
            patch.object(mp, "_registry_get_by_mac", return_value=None),
        ):
            mp._registry_claim(
                "aa:bb:cc:dd:ee:ff",
                "GBO-GW-0001",
                site="GBO",
                operator="cc:comfort",
                is_test=True,
            )
        item = ddb.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["is_test"], {"BOOL": True})

    def test_first_canary_allocation_rejects_multiple_gateways(self):
        request = mp.GatewayBatchRequest(
            site_code="GBO",
            units=[
                mp.GatewayUnit(pcb_mac="aa:bb:cc:dd:ee:01"),
                mp.GatewayUnit(pcb_mac="aa:bb:cc:dd:ee:02"),
            ],
            wifi_ssid="site-starlink",
            wifi_password="secret",
            canary=True,
        )
        with patch.object(mp, "_active_site_map", return_value={"GBO": "Gbo"}):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.provision_gateway_batch(request, MagicMock(user_id="comfort"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("exactly one", ctx.exception.detail)


class TestFactoryPromotion(unittest.TestCase):
    def test_create_ota_uses_site_release_and_tracks_target(self):
        iot = MagicMock()
        iot.create_ota_update.return_value = {
            "otaUpdateId": "ota-123",
            "awsIotJobId": "AFR_OTA-ota-123",
            "otaUpdateStatus": "CREATE_PENDING",
        }
        conn = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        registry_row = {
            "site": {"S": "GBO"},
            "is_test": {"BOOL": False},
        }
        user = MagicMock(user_id="comfort")
        fake_customer_api = types.SimpleNamespace(get_connection=MagicMock(return_value=cm))

        with (
            patch.object(mp, "_ota_release", return_value=approved_release()),
            patch.object(mp, "_registry_get_by_thing", return_value=[registry_row]),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
            patch.object(mp, "_client", return_value=iot),
            patch.dict("sys.modules", {"customer_api": fake_customer_api}),
            patch.object(mp, "try_log_mutation"),
        ):
            result = mp.promote_factory_gateways(
                mp.OtaPromotionRequest(
                    site_code="GBO",
                    thing_names=["GBO-GW-0001"],
                    note="canary",
                ),
                user,
            )

        self.assertEqual(result["target_version"], "1.2.3")
        self.assertEqual(result["site_code"], "GBO")
        kwargs = iot.create_ota_update.call_args.kwargs
        self.assertEqual(
            kwargs["targets"],
            ["arn:aws:iot:us-east-1:758201218523:thing/GBO-GW-0001"],
        )
        file_entry = kwargs["files"][0]
        self.assertEqual(file_entry["fileLocation"]["s3Location"]["version"], "immutable-version-id")
        self.assertEqual(file_entry["fileVersion"], "1.2.3")
        self.assertNotIn("password", json.dumps(kwargs).lower())
        sql_args = conn.cursor.return_value.execute.call_args_list[0].args[1]
        self.assertEqual(sql_args[1], "1.2.3")

    def test_wrong_registry_site_is_rejected(self):
        row = {"site": {"S": "SAM"}, "is_test": {"BOOL": False}}
        with (
            patch.object(mp, "_ota_release", return_value=approved_release("GBO")),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
            patch.object(mp, "_registry_get_by_thing", return_value=[row]),
        ):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.promote_factory_gateways(
                    mp.OtaPromotionRequest(
                        site_code="GBO",
                        thing_names=["SAM-GW-0001"],
                    ),
                    MagicMock(user_id="comfort"),
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_anti_rollback_rejects_factory_or_older_target(self):
        release = approved_release()
        release["target_firmware_version"] = "1.1.56"
        release["factory_baseline_version"] = "1.1.56"
        with patch.object(mp, "_client"):
            checks = mp._ota_release_checks(release)
        self.assertFalse(checks["anti_rollback"]["ok"])
        self.assertIn("strictly newer", checks["anti_rollback"]["error"])

    def test_candidate_only_release_blocks_batch_promotion(self):
        release = approved_release()
        release["canary_only"] = True
        with (
            patch.object(mp, "_ota_release", return_value=release),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
        ):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.promote_factory_gateways(
                    mp.OtaPromotionRequest(
                        site_code="GBO",
                        thing_names=["GBO-GW-0001"],
                    ),
                    MagicMock(user_id="comfort"),
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("candidate-only", ctx.exception.detail)

    def test_canary_requires_exact_typed_confirmation(self):
        with (
            patch.object(mp, "_ota_release", return_value=approved_release()),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
        ):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.promote_factory_gateways(
                    mp.OtaPromotionRequest(
                        site_code="GBO",
                        thing_names=["HQTEST-1"],
                        canary=True,
                        confirmation="yes",
                    ),
                    MagicMock(user_id="comfort"),
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("CANARY HQTEST-1", ctx.exception.detail)

    def test_canary_rejects_non_test_non_allowlisted_gateway(self):
        row = {"site": {"S": "GBO"}, "is_test": {"BOOL": False}}
        with (
            patch.object(mp, "_ota_release", return_value=approved_release()),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
            patch.object(mp, "_registry_get_by_thing", return_value=[row]),
            patch.object(mp, "OTA_CANARY_THINGS", set()),
        ):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.promote_factory_gateways(
                    mp.OtaPromotionRequest(
                        site_code="GBO",
                        thing_names=["GBO-GW-0001"],
                        canary=True,
                        confirmation="CANARY GBO-GW-0001",
                    ),
                    MagicMock(user_id="comfort"),
                )
        self.assertEqual(ctx.exception.status_code, 403)


class TestReleaseApproval(unittest.TestCase):
    def test_successful_canary_can_be_approved_with_audited_validation_waiver(self):
        candidate = approved_release()
        candidate["canary_only"] = True
        candidate["catalog_canary_only"] = True
        approved = dict(candidate)
        approved["canary_only"] = False
        approved["approval"] = {"approved_by": "engineer"}

        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("GBO-GW-0001", "GBO", "SUCCEEDED", "1.2.3", True)
        ]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        fake_customer_api = types.SimpleNamespace(get_connection=MagicMock(return_value=cm))

        with (
            patch.object(mp, "_active_site_map", return_value={"GBO": "Gbo"}),
            patch.object(mp, "_ota_release", side_effect=[candidate, approved]),
            patch.object(mp, "_ota_missing_config", return_value=[]),
            patch.object(
                mp,
                "_ota_release_checks",
                return_value={
                    "anti_rollback": {"ok": True},
                    "artifact": {"ok": True},
                    "signing_profile": {"ok": True},
                },
            ),
            patch.dict("sys.modules", {"customer_api": fake_customer_api}),
            patch.object(mp, "try_log_mutation"),
        ):
            result = mp.approve_ota_release(
                mp.OtaReleaseApprovalRequest(
                    site_code="GBO",
                    canary_ota_update_id="ota-canary-1",
                    waive_physical_validation=True,
                    waiver_reason="Meter bench is unavailable; OTA-only proof approved.",
                    confirmation="APPROVE GBO 1.2.3",
                ),
                MagicMock(user_id="engineer"),
            )

        self.assertTrue(result["ready"])
        conn.commit.assert_called_once()
        insert_sql = cursor.execute.call_args_list[-1].args[0]
        self.assertIn("onemeter_ota_release_approvals", insert_sql)


class TestActivationWalkthrough(unittest.TestCase):
    def test_physical_step_requires_evidence(self):
        with patch.object(mp, "_active_site_map", return_value={"GBO": "Gbo"}):
            with self.assertRaises(mp.HTTPException) as ctx:
                mp.update_activation_step(
                    mp.ActivationStepUpdateRequest(
                        site_code="GBO",
                        step_key="meter_string_ready",
                        completed=True,
                        evidence_note="",
                    ),
                    MagicMock(user_id="comfort"),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_operator_confirmation_is_persisted_and_audited(self):
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        fake_customer_api = types.SimpleNamespace(get_connection=MagicMock(return_value=cm))

        with (
            patch.object(mp, "_active_site_map", return_value={"GBO": "Gbo"}),
            patch.dict("sys.modules", {"customer_api": fake_customer_api}),
            patch.object(mp, "try_log_mutation") as audit,
        ):
            result = mp.update_activation_step(
                mp.ActivationStepUpdateRequest(
                    site_code="gbo",
                    step_key="test_customer_assigned",
                    completed=True,
                    evidence_note="TEST-GBO-001 / meter 240017",
                ),
                MagicMock(user_id="comfort"),
            )

        self.assertTrue(result["completed"])
        self.assertEqual(result["site_code"], "GBO")
        self.assertIn("onemeter_activation_steps", cursor.execute.call_args.args[0])
        conn.commit.assert_called_once()
        audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
