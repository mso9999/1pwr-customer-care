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


if __name__ == "__main__":
    unittest.main()
