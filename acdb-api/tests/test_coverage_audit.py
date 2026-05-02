"""
Unit tests for the coverage audit pure helpers.

We import the ops script (``scripts/ops/audit_coverage_gaps.py``) directly
so the math the in-CC ``coverage_audit.py`` relies on stays consistent
with the systemd-timer path. No DB / network / FastAPI required.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OPS_DIR = os.path.join(REPO_ROOT, "scripts", "ops")
if OPS_DIR not in sys.path:
    sys.path.insert(0, OPS_DIR)

import audit_coverage_gaps as audit  # noqa: E402  (path tweak above)


class DetectMonthlyDeficitsTests(unittest.TestCase):
    """The deficit detector is the part of the audit that's most sensitive
    to off-by-one and "current month is in progress" mistakes. RCA-pinned.
    """

    def setUp(self):
        self.now = datetime(2026, 5, 2, 6, 0, tzinfo=timezone.utc)  # day 2 of May
        # Six healthy months, then May (in-progress) with low row count.
        self.coverage = {
            "MAK": {
                "2025-11": {"rows": 100_000, "meters": 200},
                "2025-12": {"rows": 100_000, "meters": 200},
                "2026-01": {"rows": 100_000, "meters": 200},
                "2026-02": {"rows": 100_000, "meters": 200},
                "2026-03": {"rows": 100_000, "meters": 200},
                "2026-04": {"rows": 100_000, "meters": 200},
                "2026-05": {"rows": 7_000, "meters": 200},  # ~7K of expected 6.5K (2/31 of 100K)
            },
        }

    def test_in_progress_month_not_flagged_when_prorated_healthy(self):
        # 2/31 of 100K = ~6,452. We have 7,000 -> OK.
        out = audit.detect_monthly_deficits(self.coverage, now=self.now)
        for d in out:
            if d["site"] == "MAK" and d["month"] == "2026-05":
                self.fail(f"In-progress May should not be flagged, got: {d}")

    def test_in_progress_month_flagged_when_prorated_low(self):
        # Drop May rows to 1,000 -> 1000 / 6452 = 15.5% -> 84.5% missing -> flagged.
        cov = dict(self.coverage)
        cov["MAK"] = dict(cov["MAK"])
        cov["MAK"]["2026-05"] = {"rows": 1000, "meters": 200}
        out = audit.detect_monthly_deficits(cov, now=self.now)
        in_prog = [d for d in out if d["in_progress"]]
        self.assertEqual(len(in_prog), 1)
        self.assertEqual(in_prog[0]["site"], "MAK")
        self.assertEqual(in_prog[0]["month"], "2026-05")
        self.assertGreater(in_prog[0]["missing_pct"], 50)

    def test_complete_month_deficit_flagged(self):
        # Knock March down to 1K rows -> baseline median (others ~100K) -> flagged.
        cov = dict(self.coverage)
        cov["MAK"] = dict(cov["MAK"])
        cov["MAK"]["2026-03"] = {"rows": 1_000, "meters": 200}
        out = audit.detect_monthly_deficits(cov, now=self.now)
        complete = [d for d in out if not d["in_progress"]]
        self.assertTrue(any(d["site"] == "MAK" and d["month"] == "2026-03" for d in complete))

    def test_current_month_excluded_from_baseline(self):
        # If May had a wildly high count it shouldn't drag the baseline up
        # and mask a real deficit elsewhere.
        cov = dict(self.coverage)
        cov["MAK"] = dict(cov["MAK"])
        cov["MAK"]["2026-05"] = {"rows": 5_000_000, "meters": 200}  # absurd
        cov["MAK"]["2026-04"] = {"rows": 30_000, "meters": 200}      # 30% of 100K -> deficit
        out = audit.detect_monthly_deficits(cov, now=self.now)
        complete = [d for d in out if not d["in_progress"]]
        self.assertTrue(any(d["site"] == "MAK" and d["month"] == "2026-04" for d in complete))

    def test_too_few_months_skipped(self):
        # Only 2 complete months for a site -> not enough baseline; skip
        # entirely (don't blow up).
        cov = {
            "MAK": {
                "2026-04": {"rows": 100_000, "meters": 200},
                "2026-05": {"rows": 1, "meters": 200},
            }
        }
        out = audit.detect_monthly_deficits(cov, now=self.now)
        # Allow either 0 or 1 -- but must not crash. The in-progress
        # branch may still emit if the single complete month is non-zero.
        for d in out:
            self.assertIn(d["site"], cov)


class SummarizeZeroCoverageTests(unittest.TestCase):
    def test_basic_rollup(self):
        zero = [
            {"community": "MAK"}, {"community": "MAK"}, {"community": "MAK"},
            {"community": "SHG"},
        ]
        active = {"MAK": 100, "SHG": 50}
        out = audit.summarize_zero_coverage(zero, active)
        self.assertEqual(out["MAK"]["zero_coverage_meters"], 3)
        self.assertEqual(out["MAK"]["zero_coverage_pct"], 3.0)
        self.assertEqual(out["SHG"]["zero_coverage_meters"], 1)
        self.assertEqual(out["SHG"]["zero_coverage_pct"], 2.0)

    def test_unknown_active_count(self):
        out = audit.summarize_zero_coverage([{"community": "MYSTERY"}], {})
        self.assertEqual(out["MYSTERY"]["active_meters"], 0)
        self.assertIsNone(out["MYSTERY"]["zero_coverage_pct"])


class MedianTests(unittest.TestCase):
    def test_odd(self):
        self.assertEqual(audit._median([1, 2, 3]), 2)

    def test_even(self):
        self.assertEqual(audit._median([1, 2, 3, 4]), 2.5)

    def test_empty(self):
        self.assertIsNone(audit._median([]))

    def test_with_nones(self):
        self.assertEqual(audit._median([1, None, 3, None, 5]), 3)


class RenderMarkdownTests(unittest.TestCase):
    """Smoke test the renderer doesn't crash on the canonical payload shape."""

    def test_minimal_payload_renders(self):
        payload = {
            "country": "LS",
            "database_label": "test",
            "generated_at": "2026-05-02T00:00:00+00:00",
            "window_months": 8,
            "stale_days": 30,
            "deficit_threshold": 0.5,
            "active_counts": {"MAK": 100},
            "monthly_coverage": {"MAK": {"2026-04": {"rows": 1000, "meters": 50}}},
            "monthly_deficits": [],
            "zero_coverage_meters": [],
            "zero_coverage_summary": {},
            "stale_meters": [],
            "last_ingest": {"MAK": {"thundercloud": {"last_reading": "2026-04-30T00:00:00+00:00",
                                                     "last_insert": "2026-05-01T00:00:00+00:00",
                                                     "rows_total": 1000}}},
            "cross_country_meters": [],
            "declared_sites_missing_data": [],
            "orphan_sites": [],
            "totals": {"active_meters": 100, "zero_coverage_meters": 0, "stale_meters": 0,
                       "monthly_deficits_flagged": 0, "sites_with_active_meters": 1, "sites_with_data": 1},
        }
        md = audit.render_markdown(payload)
        self.assertIn("# 1PDB coverage audit — LS", md)
        self.assertIn("MAK", md)
        self.assertIn("Last ingest", md)


if __name__ == "__main__":
    unittest.main()
