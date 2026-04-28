"""Tests for the mobile app BFF (`/api/app/*`)."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import Response

import app_bff
from country_config import BENIN, LESOTHO


def _call_active_countries() -> tuple[dict, Response]:
    """Invoke the route handler directly with a fresh ``Response`` object."""
    response = Response()
    body = app_bff.active_countries(response)
    return body, response


class TestActiveCountries(unittest.TestCase):
    def test_returns_both_active_countries_with_expected_shape(self):
        body, _ = _call_active_countries()
        self.assertIn("countries", body)
        countries = body["countries"]
        codes = sorted(row["countryCode"] for row in countries)
        self.assertEqual(codes, ["BN", "LS"])
        for row in countries:
            self.assertIn("countryCode", row)
            self.assertIn("displayName", row)
            self.assertIn("active", row)
            self.assertTrue(row["active"])
            self.assertIsInstance(row["displayName"], str)
            self.assertGreater(len(row["displayName"]), 0)
            # appConfigUrl is omitted in v1
            self.assertNotIn("appConfigUrl", row)

    def test_display_name_uses_field_then_falls_back_to_name(self):
        bn_no_display = replace(BENIN, display_name=None)
        with patch.object(
            app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": bn_no_display}
        ):
            body, _ = _call_active_countries()
        rows = {r["countryCode"]: r for r in body["countries"]}
        # LS keeps its display_name
        self.assertEqual(rows["LS"]["displayName"], LESOTHO.display_name)
        # BN falls back to name when display_name is None
        self.assertEqual(rows["BN"]["displayName"], BENIN.name)

    def test_inactive_country_is_filtered_out(self):
        bn_inactive = replace(BENIN, active=False)
        with patch.object(
            app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": bn_inactive}
        ):
            body, _ = _call_active_countries()
        codes = [r["countryCode"] for r in body["countries"]]
        self.assertEqual(codes, ["LS"])

    def test_appconfigurl_included_when_remote_pack_configured(self):
        remote_urls = {"BN": "https://cc.1pwrafrica.com/api/app/country-config/BN"}
        with patch.object(app_bff, "_REGISTRY", {"LS": LESOTHO, "BN": BENIN}):
            with patch.object(app_bff, "_REMOTE_CONFIG_URLS", remote_urls):
                body, _ = _call_active_countries()
        rows = {r["countryCode"]: r for r in body["countries"]}
        self.assertEqual(rows["BN"]["appConfigUrl"], remote_urls["BN"])
        self.assertNotIn("appConfigUrl", rows["LS"])

    def test_response_is_cacheable(self):
        _, response = _call_active_countries()
        cache_control = response.headers.get("cache-control", "")
        self.assertIn("max-age=300", cache_control)
        self.assertIn("public", cache_control)

    def test_router_prefix_and_route_registered(self):
        self.assertEqual(app_bff.router.prefix, "/api/app")
        paths = [route.path for route in app_bff.router.routes]
        self.assertIn("/api/app/active-countries", paths)


if __name__ == "__main__":
    unittest.main()
