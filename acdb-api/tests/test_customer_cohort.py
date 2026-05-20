"""Unit tests for customer_cohort SQL builder and validators.

No live DB.  Verifies parameterisation, sort-column whitelist, customer-type
expansion, status filtering and pagination boundaries.
"""

from __future__ import annotations

import os
import sys
import unittest

# Make ``acdb-api`` importable when tests are run from the repo root.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from customer_cohort import (  # noqa: E402
    COHORT_STATUSES,
    CohortExportRequest,
    CohortFilters,
    CohortQuery,
    _build_export_query,
    _build_query,
    _expand_customer_types,
    _resolve_export_columns,
    _resolve_fee_threshold,
    _resolve_sites,
)


class TestStatusTaxonomy(unittest.TestCase):
    def test_canonical_six_buckets(self):
        self.assertEqual(
            COHORT_STATUSES,
            [
                "not_paid",
                "partially_paid_not_connected",
                "partially_paid_connected",
                "fully_paid_not_connected",
                "fully_paid_connected",
                "terminated",
            ],
        )


class TestCustomerTypeExpansion(unittest.TestCase):
    def test_hh_expands_to_three(self):
        self.assertEqual(_expand_customer_types(["HH"]), ["HH1", "HH2", "HH3"])

    def test_specific_types_pass_through_uppercased(self):
        self.assertEqual(_expand_customer_types(["sme", "Gov"]), ["SME", "GOV"])

    def test_empty_returns_empty(self):
        self.assertEqual(_expand_customer_types(None), [])
        self.assertEqual(_expand_customer_types([]), [])


class TestSiteResolution(unittest.TestCase):
    def test_explicit_sites_validated_against_known(self):
        sites = _resolve_sites(None, ["MAK"])
        self.assertIn("MAK", sites)

    def test_country_resolves_to_all_sites(self):
        sites = _resolve_sites("LS", None)
        self.assertGreater(len(sites), 0)
        # Sorted alphabetically
        self.assertEqual(sites, sorted(sites))

    def test_invalid_site_raises(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            _resolve_sites(None, ["NOT_A_SITE"])


class TestQueryBuilder(unittest.TestCase):
    def test_basic_query_no_filters(self):
        q = CohortQuery(filters=CohortFilters(country="LS"))
        sql, params = _build_query(q, count_only=False)
        # WITH ... cohort ... + paginated SELECT
        self.assertIn("WITH paid_totals AS", sql)
        self.assertIn("payments_connection_fee", sql)
        self.assertIn("payments_fee_repayment_via_electricity", sql)
        self.assertIn("cohort_status", sql)
        self.assertIn("LIMIT %s OFFSET %s", sql)
        # Site placeholders + 2x threshold + page_size + offset
        self.assertGreaterEqual(len(params), 4)

    def test_param_count_matches_placeholder_count(self):
        """Regression for the 500 caused by param/placeholder misalignment.

        The number of ``%s`` placeholders in the assembled SQL must equal the
        number of params we pass to ``cursor.execute``.  We exercise all
        optional clauses (sites, customer types, search, statuses) at once.
        """
        q = CohortQuery(
            filters=CohortFilters(
                country="LS",
                customer_types=["HH", "SME"],
                statuses=["not_paid", "fully_paid_connected"],
                search="Mok",
            ),
            page=2,
            page_size=25,
        )
        sql, params = _build_query(q, count_only=False)
        self.assertEqual(sql.count("%s"), len(params))

        count_sql, count_params = _build_query(q, count_only=True)
        self.assertEqual(count_sql.count("%s"), len(count_params))

    def test_threshold_params_come_first(self):
        """The two fee_threshold %s appear inside the CTE SELECT before any
        sites/customer_type/search placeholders, so they must be at the head
        of the params tuple."""
        q = CohortQuery(filters=CohortFilters(country="LS"))
        _, params = _build_query(q, count_only=False)
        # First two params are numeric (the threshold), not a site code.
        self.assertIsInstance(params[0], (int, float))
        self.assertIsInstance(params[1], (int, float))
        self.assertEqual(params[0], params[1])
        # Third param is a site code (string).
        self.assertIsInstance(params[2], str)

    def test_status_filter_adds_in_clause(self):
        q = CohortQuery(
            filters=CohortFilters(country="LS", statuses=["not_paid", "fully_paid_connected"]),
        )
        sql, params = _build_query(q, count_only=False)
        self.assertIn("cohort_status IN", sql)
        # 'not_paid' + 'fully_paid_connected' both appear in params
        self.assertIn("not_paid", params)
        self.assertIn("fully_paid_connected", params)

    def test_invalid_status_silently_dropped(self):
        q = CohortQuery(
            filters=CohortFilters(country="LS", statuses=["not_paid", "drop-table"]),
        )
        sql, params = _build_query(q, count_only=False)
        self.assertIn("not_paid", params)
        self.assertNotIn("drop-table", params)

    def test_customer_type_filter(self):
        q = CohortQuery(filters=CohortFilters(country="LS", customer_types=["HH", "SME"]))
        sql, params = _build_query(q, count_only=False)
        self.assertIn("UPPER(TRIM(c.customer_type))", sql)
        for ct in ("HH1", "HH2", "HH3", "SME"):
            self.assertIn(ct, params)

    def test_search_clause(self):
        q = CohortQuery(filters=CohortFilters(country="LS", search="Mok"))
        sql, params = _build_query(q, count_only=False)
        self.assertIn("first_name ILIKE", sql)
        self.assertIn("%Mok%", params)

    def test_count_query_does_not_paginate(self):
        q = CohortQuery(filters=CohortFilters(country="LS"))
        sql, _ = _build_query(q, count_only=True)
        self.assertIn("COUNT(*)", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertNotIn("OFFSET", sql)

    def test_page_size_capped(self):
        q = CohortQuery(filters=CohortFilters(country="LS"), page_size=9999)
        _, params = _build_query(q, count_only=False)
        # page_size is the second-to-last param, offset is the last
        self.assertEqual(params[-2], 500)

    def test_page_size_zero_falls_back_to_default(self):
        """page_size=0 should not produce a zero LIMIT; falls back to default 50."""
        q = CohortQuery(filters=CohortFilters(country="LS"), page_size=0)
        _, params = _build_query(q, count_only=False)
        self.assertEqual(params[-2], 50)

    def test_offset_computation(self):
        q = CohortQuery(filters=CohortFilters(country="LS"), page=3, page_size=25)
        _, params = _build_query(q, count_only=False)
        self.assertEqual(params[-1], 50)  # (page-1) * page_size

    def test_sort_dir_normalised(self):
        q = CohortQuery(
            filters=CohortFilters(country="LS"),
            sort_by="total_paid",
            sort_dir="DESC",
        )
        sql, _ = _build_query(q, count_only=False)
        self.assertIn("ORDER BY total_paid DESC", sql)

    def test_every_sort_key_references_cohort_cte_column(self):
        """Regression for the 500 caused by ``ORDER BY community`` — the
        outer SELECT runs against the ``cohort`` CTE, whose projection
        renames ``c.community`` to ``site``.  Every value in ``_SORT_COLS``
        must therefore be an identifier present in the CTE's SELECT list.
        """
        import re
        from customer_cohort import _SORT_COLS

        # Columns emitted by the cohort CTE (kept in sync with _build_query).
        cohort_cte_columns = {
            "customer_id",
            "first_name",
            "last_name",
            "phone",
            "site",
            "customer_type",
            "date_service_connected",
            "date_service_terminated",
            "payment_status_override",
            "account_number",
            "total_paid",
            "payments_connection_fee",
            "payments_readyboard_fee",
            "payments_fee_repayment_via_electricity",
            "payments_electricity",
            "cohort_status",
        }
        for ui_key, sql_expr in _SORT_COLS.items():
            for ident in re.split(r"[,\s]+", sql_expr.strip()):
                if not ident:
                    continue
                self.assertIn(
                    ident,
                    cohort_cte_columns,
                    f"sort key {ui_key!r} → {sql_expr!r} references "
                    f"unknown CTE column {ident!r}",
                )


class TestExportColumns(unittest.TestCase):
    def test_mandatory_always_included(self):
        cols = _resolve_export_columns(["site"])
        self.assertEqual(cols[0], "account_number")
        self.assertEqual(cols[1], "name")
        self.assertIn("site", cols)

    def test_unknown_columns_ignored(self):
        cols = _resolve_export_columns(["gender", "not_a_column"])
        self.assertIn("gender", cols)
        self.assertNotIn("not_a_column", cols)

    def test_export_sql_includes_extra_attributes(self):
        body = CohortExportRequest(
            filters=CohortFilters(country="LS"),
            columns=["gender", "gps_lat", "gps_lon"],
        )
        cols = _resolve_export_columns(body.columns)
        sql, params = _build_export_query(body, cols)
        self.assertIn("gender", sql)
        self.assertIn("gps_lat", sql)
        self.assertIn("LIMIT %s", sql)
        self.assertEqual(params[-1], 50_000)


class TestFeeThreshold(unittest.TestCase):
    def test_ls_threshold_positive(self):
        self.assertGreater(_resolve_fee_threshold("LS"), 0)

    def test_unknown_country_falls_back(self):
        self.assertEqual(_resolve_fee_threshold("ZZ"), 1.0)


if __name__ == "__main__":
    unittest.main()
