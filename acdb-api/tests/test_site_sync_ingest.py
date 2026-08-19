"""Tests for the PR → CC site-sync ingest endpoint."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import country_config
import customer_api  # noqa: F401  (assemble app so router imports resolve)
import site_sync_ingest


def _fake_conn(row_fn):
    """Minimal psycopg2 stand-in: row_fn(sql, params) -> rows for SELECT."""
    class Cur:
        def __init__(self):
            self.rowcount = 1

        def execute(self, sql, params=None):
            self._rows = row_fn(" ".join(sql.lower().split()), params or ())

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return Cur()

        def commit(self):
            pass

    return Conn()


def _event(**site_over):
    site = {
        "organizationId": "1pwr_zambia",
        "countryCode": "ZMB",
        "code": "CHI",
        "name": "Chinsali",
        "active": True,
        "district": "Muchinga",
        "ugpProjects": [{"ugpProjectId": "CHI_minigrid"}],
        "canonicalUgpProjectId": "CHI_minigrid",
    }
    site.update(site_over)
    return site_sync_ingest.SiteEventIn(
        source="pr_admin",
        eventType="site.created",
        site=site_sync_ingest.SitePayloadIn(**site),
        idempotencyKey="key-1",
        updatedAt="2026-08-19T00:00:00Z",
    )


class TestSiteSyncIngest(unittest.TestCase):
    def setUp(self):
        self._key = "test-sync-key"
        os.environ["CC_SITE_SYNC_API_KEY"] = self._key
        country_config.reset_live_site_cache()

    def tearDown(self):
        os.environ.pop("CC_SITE_SYNC_API_KEY", None)
        country_config.reset_live_site_cache()

    def test_rejects_bad_key(self):
        with self.assertRaises(Exception) as ctx:
            site_sync_ingest.ingest_site_event(_event(), x_api_key="wrong")
        assert getattr(ctx.exception, "status_code", None) == 401

    def test_ignores_non_operating_org(self):
        out = site_sync_ingest.ingest_site_event(
            _event(organizationId="mgb", countryCode=""), x_api_key=self._key
        )
        assert out["applied"] is False
        assert out["reason"] == "non_operating_org"

    def test_ignores_non_three_letter_code(self):
        out = site_sync_ingest.ingest_site_event(_event(code="HQ"), x_api_key=self._key)
        assert out["applied"] is False
        assert out["reason"] == "code_not_three_letters"

    def test_ignores_other_lane(self):
        # Event for Lesotho hitting the Zambia lane.
        with patch.object(country_config, "COUNTRY", country_config.ZAMBIA):
            out = site_sync_ingest.ingest_site_event(
                _event(organizationId="1pwr_lesotho", countryCode="LSO"), x_api_key=self._key
            )
        assert out["applied"] is False
        assert out["reason"] == "other_lane"

    def test_creates_staged_inactive_with_ugp_link(self):
        writes = []

        def rows(sql, params):
            if sql.startswith("select"):
                return []
            writes.append((sql, params))
            return []

        with (
            patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
            patch("customer_api.get_connection", return_value=_fake_conn(rows)),
        ):
            out = site_sync_ingest.ingest_site_event(_event(), x_api_key=self._key)

        assert out == {"ok": True, "applied": True, "action": "staged"}
        insert = next(w for w in writes if w[0].startswith("insert into country_sites"))
        # active/source/created_by are inline literals (FALSE, 'pr', 'pr-site-sync')
        assert "false" in insert[0] and "'pr'" in insert[0]
        params = insert[1]
        assert params[:4] == ("ZM", "CHI", "Chinsali", "Muchinga")
        assert "CHI_minigrid" in params[4]  # ugp_project_ids json
        assert params[5] == "CHI_minigrid"

    def test_update_preserves_local_activation(self):
        writes = []

        def rows(sql, params):
            if "from country_sites where country_code" in sql:
                return [(True, "pr")]  # already active locally
            return []

        class Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                norm = " ".join(sql.lower().split())
                if norm.startswith("update") or norm.startswith("insert"):
                    writes.append((norm, params))
                self._rows = rows(norm, params or ())

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return self._rows

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cur()

            def commit(self):
                pass

        with (
            patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
            patch("customer_api.get_connection", return_value=Conn()),
        ):
            out = site_sync_ingest.ingest_site_event(
                _event(), x_api_key=self._key
            )

        assert out["action"] == "updated"
        update = next(w for w in writes if w[0].startswith("update country_sites"))
        # Update touches name/district/ugp only — never the active flag.
        assert "active" not in update[0]

    def test_deactivated_retires_row(self):
        writes = []

        def rows(sql, params):
            if "from country_sites where country_code" in sql:
                return [(True, "pr")]
            return []

        class Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                norm = " ".join(sql.lower().split())
                if norm.startswith("update") or norm.startswith("insert"):
                    writes.append((norm, params))
                self._rows = rows(norm, params or ())

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return self._rows

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cur()

            def commit(self):
                pass

        ev = _event()
        ev.eventType = "site.deactivated"
        with (
            patch.object(country_config, "COUNTRY", country_config.ZAMBIA),
            patch("customer_api.get_connection", return_value=Conn()),
        ):
            out = site_sync_ingest.ingest_site_event(ev, x_api_key=self._key)

        assert out["action"] == "deactivated"
        update = next(w for w in writes if "active = false" in w[0])
        assert "retired_by = 'pr-site-sync'" in update[0]


if __name__ == "__main__":
    unittest.main()
