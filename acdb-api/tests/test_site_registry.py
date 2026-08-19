"""Unit tests for the UI-managed canonical site registry (migration 064)."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import country_config
import customer_api  # noqa: F401  (assemble app so router imports resolve)
import site_registry


def _fake_conn(rows_by_query):
    """Build a mock connection whose cursor returns canned rows per query."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    def execute(sql, params=None):
        cur._last_sql = " ".join(str(sql).split()).lower()
        cur._last_params = params
        cur._rows = rows_by_query(cur._last_sql, params)

    cur.execute.side_effect = execute
    cur.fetchall.side_effect = lambda: getattr(cur, "_rows", [])
    cur.fetchone.side_effect = lambda: (getattr(cur, "_rows", []) or [None])[0]
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


class TestLiveSiteOverlay(unittest.TestCase):
    def setUp(self):
        country_config.reset_live_site_cache()

    def tearDown(self):
        country_config.reset_live_site_cache()

    def test_static_config_when_table_missing(self):
        with patch.object(country_config, "_db_site_rows", return_value={}):
            assert country_config.live_site_abbrev("ZM") == {}
            ls = country_config.live_site_abbrev("LS")
            assert "MAK" in ls

    def test_db_rows_overlay_active_country(self):
        rows = {"ZM": {"CHI": {"name": "Chinsali", "district": "Muchinga"}}}
        with patch.object(country_config, "_db_site_rows", return_value=rows):
            zm = country_config.live_site_abbrev("ZM")
            assert zm == {"CHI": "Chinsali"}
            assert country_config.live_known_sites("ZM") == {"CHI"}
            districts = country_config.live_site_districts("ZM")
            assert districts == {"CHI": "Muchinga"}

    def test_overlay_never_leaks_across_countries(self):
        rows = {"ZM": {"CHI": {"name": "Chinsali", "district": None}}}
        with patch.object(country_config, "_db_site_rows", return_value=rows):
            ls = country_config.live_site_abbrev("LS")
            assert "CHI" not in ls
            assert "MAK" in ls

    def test_db_row_can_rename_but_not_remove_static_site(self):
        rows = {"LS": {"MAK": {"name": "Makhaleng (HQ)", "district": None}}}
        with patch.object(country_config, "_db_site_rows", return_value=rows):
            ls = country_config.live_site_abbrev("LS")
            assert ls["MAK"] == "Makhaleng (HQ)"


class TestSiteRegistryApi(unittest.TestCase):
    def setUp(self):
        country_config.reset_live_site_cache()

    def tearDown(self):
        country_config.reset_live_site_cache()

    def _user(self):
        return SimpleNamespace(email="eng@1pwrafrica.com", user_id="1PWR999")

    def test_create_rejects_malformed_code(self):
        with self.assertRaises(Exception) as ctx:
            site_registry.create_country_site(
                site_registry.SiteCreate(code="AB1", name="Bad"), self._user()
            )
        assert getattr(ctx.exception, "status_code", None) == 400

    def test_create_rejects_config_defined_code(self):
        with patch.object(country_config, "COUNTRY", country_config.ZAMBIA):
            with self.assertRaises(Exception) as ctx:
                site_registry.create_country_site(
                    site_registry.SiteCreate(code="MAK", name="Shadow"), self._user()
                )
            assert getattr(ctx.exception, "status_code", None) == 409

    def test_create_rejects_globally_duplicate_code(self):
        def rows(sql, params):
            if "from country_sites where code" in sql:
                return [("ZM", True)]
            return []

        with (
            patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
            patch("customer_api.get_connection", return_value=_fake_conn(rows)),
            self.assertRaises(Exception) as ctx,
        ):
            site_registry.create_country_site(
                site_registry.SiteCreate(code="CHI", name="Chinsali"), self._user()
            )
        assert getattr(ctx.exception, "status_code", None) == 409

    def test_create_inserts_and_resets_cache(self):
        inserted = {}

        def rows(sql, params):
            if sql.startswith("insert into country_sites"):
                inserted["params"] = params
                return []
            return []

        with (
            patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
            patch("customer_api.get_connection", return_value=_fake_conn(rows)),
            patch("site_registry.try_log_mutation", return_value=None),
        ):
            out = site_registry.create_country_site(
                site_registry.SiteCreate(code="CHI", name="Chinsali", district="Muchinga"),
                self._user(),
            )
        assert out["ok"] is True
        assert out["code"] == "CHI"
        assert inserted["params"][:4] == ("ZM", "CHI", "Chinsali", "Muchinga")

    def test_list_merges_config_and_db_with_config_winning(self):
        def rows(sql, params):
            if "from country_sites where country_code" in sql:
                return [
                    ("LS", "MAK", "Shadow Mak", None, True, "x", None, None, None, None, "ui"),
                    ("LS", "NEW", "New Site", "Maseru", True, "x", None, None, None, None, "ui"),
                ]
            return []

        with (
            patch.object(country_config, "COUNTRY", country_config.LESOTHO),
            patch("customer_api.get_connection", return_value=_fake_conn(rows)),
        ):
            out = site_registry.list_country_sites(self._user())
        by_code = {s["code"]: s for s in out["sites"]}
        assert by_code["MAK"]["source"] == "config"
        assert by_code["MAK"]["name"] != "Shadow Mak"
        assert by_code["NEW"]["source"] == "ui"
        assert by_code["NEW"]["district"] == "Maseru"


if __name__ == "__main__":
    unittest.main()
