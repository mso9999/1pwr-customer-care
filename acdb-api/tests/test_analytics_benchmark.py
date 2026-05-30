"""Unit tests for analytics consumption benchmark helpers."""

from __future__ import annotations

import os
import sys
import types
import unittest

# Make ``acdb-api`` importable when tests are run from repo root.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# Stub customer_api to avoid circular import during analytics module import.
stub_customer_api = types.ModuleType("customer_api")
stub_customer_api.get_connection = lambda: None  # pragma: no cover
sys.modules["customer_api"] = stub_customer_api

import analytics  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class TestBenchmarkCustomerTypes(unittest.TestCase):
    def test_hh_family_normalizes_to_hh(self):
        out = analytics._expand_customer_types(["hh1", "HH2", "HH", "sme"])
        self.assertEqual(out, ["HH", "SME"])

    def test_empty_customer_types(self):
        self.assertEqual(analytics._expand_customer_types(None), [])
        self.assertEqual(analytics._expand_customer_types([]), [])


class TestBenchmarkSiteResolution(unittest.TestCase):
    def test_rejects_portfolio_with_all_datasets(self):
        with self.assertRaises(HTTPException):
            analytics._resolve_benchmark_sites(
                country="LS",
                sites=None,
                portfolio_id="demo",
                all_datasets=True,
            )

    def test_country_scope_resolves_non_empty_sites(self):
        sites = analytics._resolve_benchmark_sites(
            country="LS",
            sites=None,
            portfolio_id=None,
            all_datasets=False,
        )
        self.assertGreater(len(sites), 0)

    def test_all_country_keyword_is_supported(self):
        sites = analytics._resolve_benchmark_sites(
            country="ALL",
            sites=None,
            portfolio_id=None,
            all_datasets=False,
        )
        self.assertGreater(len(sites), 0)

    def test_portfolio_scope_intersects_sites(self):
        original = analytics.list_portfolios
        try:
            analytics.list_portfolios = lambda: [
                {"id": "p1", "siteIds": ["MAK", "ZZZ"]},
            ]
            sites = analytics._resolve_benchmark_sites(
                country="LS",
                sites=["MAK", "MAS"],
                portfolio_id="p1",
                all_datasets=False,
            )
            self.assertEqual(sites, ["MAK"])
        finally:
            analytics.list_portfolios = original


class TestBenchmarkSqlBuilder(unittest.TestCase):
    def test_sql_placeholder_alignment(self):
        sql, params = analytics._build_consumption_benchmark_sql(
            period="month",
            resolved_sites=["MAK", "MAS"],
            customer_types=["HH", "SME"],
            date_from=analytics.date(2026, 1, 1),
            date_to=analytics.date(2026, 5, 31),
        )
        self.assertEqual(sql.count("%s"), len(params))
        self.assertIn("date_trunc('month'", sql)
        self.assertIn("denominator_by_period", sql)
        self.assertIn("connected_on < (p.period_start + interval '1 month')", sql)

    def test_week_period_sql_shape(self):
        sql, _ = analytics._build_consumption_benchmark_sql(
            period="week",
            resolved_sites=["MAK"],
            customer_types=[],
            date_from=analytics.date(2026, 1, 1),
            date_to=analytics.date(2026, 2, 1),
        )
        self.assertIn("date_trunc('week'", sql)
        self.assertIn("interval '1 week'", sql)


if __name__ == "__main__":
    unittest.main()
