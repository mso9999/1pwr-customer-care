#!/usr/bin/env python3
"""
1PDB coverage gap audit.

Read-only audit that quantifies what's in 1PDB vs what *should* be there
(based on the live ``meters`` table and a baseline window of recent
ingestion). Surfaces:

* Per-site, per-month coverage matrix (rows + distinct meters from
  ``hourly_consumption``).
* Active meters with no ``hourly_consumption`` rows ever, joined via
  ``account_number`` (robust to the post-April-2026 meter_id format
  drift between ``meter_readings`` SparkMeter serials and
  ``hourly_consumption`` numeric IDs).
* Active meters that are stale (no readings in N days, default 30).
* Last successful ingest per (site, source) pair.
* Per-month "deficit" vs the trailing-N-month baseline -- flags any
  (site, month) cell whose row count is < 50% of the baseline median
  (the 2026-05-01 dedup-bug RCA used the same heuristic post-hoc; this
  script makes it routine).

The script is read-only by design. Output is **stdout Markdown** by
default; pass ``--json`` for a machine-readable dump or ``--out PATH``
to write the Markdown report to a file.

Usage::

    # On production CC host (uses default DATABASE_URL)
    sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \\
        /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py \\
        --country LS --window-months 8 --out /tmp/coverage-audit.md

    # Locally via SSH tunnel or any psql-reachable DATABASE_URL
    DATABASE_URL=postgresql://... python3 audit_coverage_gaps.py --country LS --json

Exit codes:
  0  audit ran cleanly (regardless of how many gaps were surfaced)
  1  database error
  2  CLI / argument error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("audit_coverage")


DEFAULT_DATABASE_URL_LS = "postgresql://cc_api@localhost:5432/onepower_cc"
DEFAULT_DATABASE_URL_BN = "postgresql://cc_api@localhost:5432/onepower_bj"


# ---------------------------------------------------------------------------
# Queries -- single-pass, no destructive ops
# ---------------------------------------------------------------------------

def fetch_active_meter_counts(cur) -> Dict[str, int]:
    """Active meter count per community."""
    cur.execute(
        "SELECT community, COUNT(*) "
        "  FROM meters "
        " WHERE status = 'active' AND community IS NOT NULL AND community <> '' "
        " GROUP BY community"
    )
    return {row[0]: int(row[1]) for row in cur.fetchall()}


def fetch_monthly_coverage(cur, months_back: int) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Per-(site, month) row + meter counts for the last N months.

    Returns ``{site: {YYYY-MM: {rows, meters}}}``.
    """
    cur.execute(
        """
        SELECT community,
               to_char(date_trunc('month', reading_hour), 'YYYY-MM') AS month,
               COUNT(*)                                              AS rows,
               COUNT(DISTINCT meter_id)                              AS meters
          FROM hourly_consumption
         WHERE reading_hour >= date_trunc('month', NOW()) - (%s || ' months')::interval
           AND community IS NOT NULL AND community <> ''
         GROUP BY community, month
         ORDER BY community, month
        """,
        (months_back,),
    )
    out: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(dict)
    for site, month, rows, meters in cur.fetchall():
        out[site][month] = {"rows": int(rows), "meters": int(meters)}
    return out


def fetch_zero_coverage_meters(cur) -> List[Dict[str, Any]]:
    """Active meters with no ``hourly_consumption`` rows for their account.

    Joined on ``account_number`` (NOT ``meter_id``) to dodge the
    SparkMeter-serial-vs-numeric-ID mismatch that otherwise overstates
    the gap. Yes, this means we miss meters whose account IS covered by
    some OTHER meter -- which is the right semantic (the *account* has
    coverage, just maybe not from this specific physical meter).
    """
    cur.execute(
        """
        SELECT m.community, m.meter_id, m.account_number,
               m.role, m.customer_connect_date
          FROM meters m
         WHERE m.status = 'active'
           AND m.account_number IS NOT NULL AND m.account_number <> ''
           AND NOT EXISTS (
                SELECT 1
                  FROM hourly_consumption h
                 WHERE h.account_number = m.account_number
           )
         ORDER BY m.community, m.account_number
        """
    )
    return [
        {
            "community": r[0],
            "meter_id": r[1],
            "account_number": r[2],
            "role": r[3],
            "customer_connect_date": r[4].isoformat() if r[4] else None,
        }
        for r in cur.fetchall()
    ]


def fetch_stale_meters(cur, days: int) -> List[Dict[str, Any]]:
    """Active meters whose last hourly reading is older than *days*."""
    cur.execute(
        """
        WITH last_seen AS (
            SELECT account_number, MAX(reading_hour) AS last_reading
              FROM hourly_consumption
             GROUP BY account_number
        )
        SELECT m.community, m.account_number, m.meter_id,
               ls.last_reading
          FROM meters m
          JOIN last_seen ls ON ls.account_number = m.account_number
         WHERE m.status = 'active'
           AND ls.last_reading < NOW() - (%s || ' days')::interval
         ORDER BY ls.last_reading ASC
        """,
        (days,),
    )
    return [
        {
            "community": r[0],
            "account_number": r[1],
            "meter_id": r[2],
            "last_reading": r[3].isoformat() if r[3] else None,
            "stale_days": (datetime.now(timezone.utc) - r[3]).days if r[3] else None,
        }
        for r in cur.fetchall()
    ]


def fetch_last_ingest_per_source(cur) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Most recent ``hourly_consumption`` row per (site, source).

    Returns ``{site: {source: {last_reading, last_insert, rows_total}}}``.
    """
    cur.execute(
        """
        SELECT community, source,
               MAX(reading_hour) AS last_reading,
               MAX(created_at)   AS last_insert,
               COUNT(*)          AS rows_total
          FROM hourly_consumption
         WHERE community IS NOT NULL AND community <> ''
         GROUP BY community, source
         ORDER BY community, source
        """
    )
    out: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for site, source, last_read, last_ins, rows in cur.fetchall():
        out[site][str(source)] = {
            "last_reading": last_read.isoformat() if last_read else None,
            "last_insert": last_ins.isoformat() if last_ins else None,
            "rows_total": int(rows),
        }
    return out


def fetch_cross_country_meters(cur, this_country: str, foreign_sites: List[str]) -> List[Dict[str, Any]]:
    """Find meters whose community belongs to a different country than the
    DB they live in (e.g. a GBO meter sitting in ``onepower_cc``).
    """
    if not foreign_sites:
        return []
    cur.execute(
        "SELECT community, COUNT(*) AS meters, COUNT(DISTINCT account_number) AS accounts "
        "  FROM meters "
        " WHERE community = ANY(%s) "
        " GROUP BY community ORDER BY community",
        (foreign_sites,),
    )
    return [
        {"community": r[0], "meters": int(r[1]), "accounts": int(r[2]),
         "this_db_country": this_country}
        for r in cur.fetchall()
    ]


# ---------------------------------------------------------------------------
# Analysis -- pure functions on the fetched data
# ---------------------------------------------------------------------------

def _median(vals: List[float]) -> Optional[float]:
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    n = len(s)
    if n % 2:
        return s[n // 2]
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def detect_monthly_deficits(
    coverage: Dict[str, Dict[str, Dict[str, int]]],
    *,
    deficit_threshold: float = 0.50,
    baseline_min_months: int = 3,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Flag (site, month) cells whose row count is < ``deficit_threshold`` * median.

    Baseline is the median of the *other* months for that site (excluding
    the candidate month itself AND the in-progress current month, which
    would otherwise look like a deficit purely because it's only N days
    old). Sites with fewer than ``baseline_min_months`` data points are
    skipped.

    The in-progress current month is reported separately, with its row
    count **prorated** against (days_elapsed / days_in_month) so a
    healthy day-2-of-31 site doesn't get flagged at "97% missing".

    Outputs are sorted by deficit severity (worst first).
    """
    import calendar as _cal
    now = (now or datetime.now(timezone.utc))
    current_month_str = now.strftime("%Y-%m")
    days_in_current = _cal.monthrange(now.year, now.month)[1]
    elapsed_fraction = max(now.day / days_in_current, 1 / days_in_current)

    out: List[Dict[str, Any]] = []
    for site, by_month in coverage.items():
        all_months = sorted(by_month)
        # Exclude the current month from the baseline AND from candidate
        # set (it gets a separate prorated check below).
        complete_months = [m for m in all_months if m != current_month_str]
        if len(complete_months) < baseline_min_months + 1:
            # Still attempt the prorated check on the current month if we
            # have any baseline at all.
            if not complete_months:
                continue
        for m in complete_months:
            others = [by_month[x]["rows"] for x in complete_months if x != m]
            base = _median(others)
            if base is None or base <= 0:
                continue
            this = by_month[m]["rows"]
            ratio = this / base
            if ratio < deficit_threshold:
                out.append({
                    "site": site,
                    "month": m,
                    "rows": this,
                    "baseline_median": base,
                    "ratio": round(ratio, 3),
                    "missing_pct": round((1 - ratio) * 100, 1),
                    "in_progress": False,
                })

        # Prorated check for the current month
        if current_month_str in by_month and complete_months:
            base = _median([by_month[x]["rows"] for x in complete_months])
            this = by_month[current_month_str]["rows"]
            if base and base > 0:
                # Compare actuals against (baseline * elapsed fraction).
                expected_so_far = base * elapsed_fraction
                ratio = this / expected_so_far if expected_so_far > 0 else 1.0
                if ratio < deficit_threshold:
                    out.append({
                        "site": site,
                        "month": current_month_str,
                        "rows": this,
                        "baseline_median": base,
                        "expected_so_far": round(expected_so_far),
                        "ratio": round(ratio, 3),
                        "missing_pct": round((1 - ratio) * 100, 1),
                        "in_progress": True,
                        "elapsed_fraction": round(elapsed_fraction, 3),
                    })

    out.sort(key=lambda r: r["ratio"])
    return out


def summarize_zero_coverage(
    zero_meters: List[Dict[str, Any]],
    active_counts: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    """Roll the per-meter list up to a per-site summary."""
    by_site: Dict[str, int] = defaultdict(int)
    for m in zero_meters:
        by_site[m["community"]] += 1
    summary: Dict[str, Dict[str, Any]] = {}
    for site, n_zero in by_site.items():
        active = active_counts.get(site, 0)
        summary[site] = {
            "active_meters": active,
            "zero_coverage_meters": n_zero,
            "zero_coverage_pct": round(100.0 * n_zero / active, 1) if active else None,
        }
    return summary


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _md_table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return "_no rows_\n"
    line = lambda parts: "| " + " | ".join("" if p is None else str(p) for p in parts) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    return "\n".join([line(headers), sep] + [line(r) for r in rows]) + "\n"


def render_markdown(payload: Dict[str, Any]) -> str:
    """Render the audit payload as a Markdown report."""
    lines: List[str] = []
    lines.append(f"# 1PDB coverage audit — {payload['country']} ({payload['generated_at']})")
    lines.append("")
    lines.append(
        f"Window: last **{payload['window_months']}** months. "
        f"Stale threshold: **{payload['stale_days']}** days. "
        f"Deficit threshold: **{int(payload['deficit_threshold'] * 100)}%** of trailing median."
    )
    lines.append("")
    lines.append(f"**Database:** `{payload['database_label']}`")
    lines.append("")

    # ------- 1. Per-site overview -------
    lines.append("## 1. Per-site coverage overview")
    lines.append("")
    headers = ["Site", "Active meters", "Zero-coverage meters", "Zero %", "Stale meters (>" + str(payload["stale_days"]) + "d)"]
    rows: List[List[Any]] = []
    stale_by_site = defaultdict(int)
    for s in payload["stale_meters"]:
        stale_by_site[s["community"]] += 1
    sites = sorted(set(payload["active_counts"]) | set(payload["zero_coverage_summary"]) | set(stale_by_site))
    for s in sites:
        active = payload["active_counts"].get(s, 0)
        zsum = payload["zero_coverage_summary"].get(s, {})
        rows.append([
            s,
            active,
            zsum.get("zero_coverage_meters", 0),
            zsum.get("zero_coverage_pct"),
            stale_by_site.get(s, 0),
        ])
    lines.append(_md_table(headers, rows))

    # ------- 2. Per-month coverage matrix -------
    lines.append("## 2. Per-month coverage matrix")
    lines.append("")
    coverage = payload["monthly_coverage"]
    months = sorted({m for site_data in coverage.values() for m in site_data})
    headers = ["Site"] + months
    rows = []
    for s in sorted(coverage):
        row = [s]
        for m in months:
            cell = coverage[s].get(m)
            if cell is None:
                row.append("--")
            else:
                row.append(f"{cell['rows']:,} / {cell['meters']}m")
        rows.append(row)
    lines.append(_md_table(headers, rows))
    lines.append("_Cell format: rows / distinct meters._")
    lines.append("")

    # ------- 3. Monthly deficits -------
    lines.append(f"## 3. Monthly deficits (rows < {int(payload['deficit_threshold'] * 100)}% of baseline)")
    lines.append("")
    lines.append("In-progress months (i.e. the current month) are compared against a "
                 "**prorated** baseline so day-2-of-31 isn't reported at 97% missing.")
    lines.append("")
    deficits = payload["monthly_deficits"]
    if deficits:
        complete = [d for d in deficits if not d.get("in_progress")]
        in_progress = [d for d in deficits if d.get("in_progress")]
        if complete:
            lines.append("**Complete months below threshold:**")
            lines.append("")
            lines.append(_md_table(
                ["Site", "Month", "Rows", "Baseline median", "Missing %"],
                [[d["site"], d["month"], f"{d['rows']:,}", f"{d['baseline_median']:,.0f}", f"{d['missing_pct']}%"] for d in complete],
            ))
        if in_progress:
            lines.append("**In-progress month (prorated):**")
            lines.append("")
            lines.append(_md_table(
                ["Site", "Month", "Rows", "Expected so far", "Missing %"],
                [[d["site"], d["month"], f"{d['rows']:,}", f"{d.get('expected_so_far', 0):,}", f"{d['missing_pct']}%"] for d in in_progress],
            ))
    else:
        lines.append("_No monthly deficits flagged._")
    lines.append("")

    # ------- 4. Last ingest per (site, source) -------
    lines.append("## 4. Last ingest per (site, source)")
    lines.append("")
    li = payload["last_ingest"]
    rows = []
    for s in sorted(li):
        for src, info in sorted(li[s].items()):
            rows.append([
                s, src,
                info["last_reading"][:10] if info["last_reading"] else "--",
                info["last_insert"][:10] if info["last_insert"] else "--",
                f"{info['rows_total']:,}",
            ])
    lines.append(_md_table(["Site", "Source", "Last reading", "Last insert", "Rows total"], rows))

    # ------- 5. Zero-coverage meters (sample) -------
    lines.append("## 5. Zero-coverage meters")
    lines.append("")
    zm = payload["zero_coverage_meters"]
    lines.append(f"Total: **{len(zm)}** active meters with no `hourly_consumption` rows for their `account_number`.")
    lines.append("")
    sample = zm[:25]
    if sample:
        lines.append("First 25 (sorted by site, then account):")
        lines.append(_md_table(
            ["Site", "Account", "Meter ID", "Role", "Connect date"],
            [[m["community"], m["account_number"], m["meter_id"], m["role"], m["customer_connect_date"] or "--"] for m in sample],
        ))
        if len(zm) > 25:
            lines.append(f"... and {len(zm) - 25} more (in JSON output).")
    lines.append("")

    # ------- 6. Stale meters (sample) -------
    lines.append(f"## 6. Stale meters (>{payload['stale_days']} days since last reading)")
    lines.append("")
    sm = payload["stale_meters"]
    lines.append(f"Total: **{len(sm)}**.")
    lines.append("")
    sample = sm[:25]
    if sample:
        lines.append(_md_table(
            ["Site", "Account", "Meter ID", "Last reading", "Days stale"],
            [[m["community"], m["account_number"], m["meter_id"], m["last_reading"][:10] if m["last_reading"] else "--", m["stale_days"]] for m in sample],
        ))
        if len(sm) > 25:
            lines.append(f"... and {len(sm) - 25} more (in JSON output).")
    lines.append("")

    # ------- 7. Cross-country meters -------
    lines.append("## 7. Cross-country meters (wrong DB)")
    lines.append("")
    cc = payload["cross_country_meters"]
    if cc:
        lines.append("**These meters live in the wrong country DB** -- likely historical migration leak. "
                     "Investigate and either move or quarantine.")
        lines.append("")
        lines.append(_md_table(
            ["Foreign site", "Meters", "Accounts", "This DB"],
            [[c["community"], c["meters"], c["accounts"], c["this_db_country"]] for c in cc],
        ))
    else:
        lines.append("_No cross-country leak detected in this DB._")
    lines.append("")

    # ------- 8. Sites in country_config but missing from data -------
    lines.append("## 8. Sites declared in `country_config` but absent from `hourly_consumption`")
    lines.append("")
    declared_missing = payload["declared_sites_missing_data"]
    if declared_missing:
        lines.append(_md_table(
            ["Site", "Active meters", "Note"],
            [[s, payload["active_counts"].get(s, 0), "no hourly data ever -- pre-operational, decommissioned, or ingest gap"] for s in declared_missing],
        ))
    else:
        lines.append("_All declared sites have at least some data._")
    lines.append("")

    # ------- 9. Sites in data but not declared -------
    lines.append("## 9. Sites in data but not in `country_config` (orphans)")
    lines.append("")
    orphans = payload["orphan_sites"]
    if orphans:
        lines.append(_md_table(
            ["Site", "Active meters", "Note"],
            [[s, payload["active_counts"].get(s, 0), "data present but no country_config entry -- legacy / decommissioned / mystery"] for s in orphans],
        ))
    else:
        lines.append("_All sites with data are declared in country_config._")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _ensure_country_config_on_path() -> None:
    """Add CC backend dir to sys.path so we can read ``country_config._REGISTRY``.

    Mirrors the pattern from ``broadcast_monthly_pin.py``.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("CC_BACKEND_DIR", ""),
        "/opt/cc-portal/backend",
        os.path.normpath(os.path.join(here, "..", "..", "acdb-api")),
    ]
    for path in candidates:
        if path and os.path.isdir(path) and os.path.exists(os.path.join(path, "country_config.py")):
            if path not in sys.path:
                sys.path.insert(0, path)
            return


def _resolve_db_url(country: str, override: Optional[str]) -> str:
    if override:
        return override
    if country == "BN":
        return os.environ.get("DATABASE_URL_BN") or DEFAULT_DATABASE_URL_BN
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL_LS


def run_audit(country: str, db_url: str, *, window_months: int, stale_days: int, deficit_threshold: float) -> Dict[str, Any]:
    """Run all queries against ``db_url`` and assemble the payload dict."""
    _ensure_country_config_on_path()
    try:
        from country_config import _REGISTRY  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - dev environments without backend
        _REGISTRY = {}

    declared_sites = set()
    foreign_sites: List[str] = []
    if country.upper() in _REGISTRY:
        declared_sites = set(_REGISTRY[country.upper()].site_abbrev)
    for cc, cfg in _REGISTRY.items():
        if cc.upper() != country.upper():
            foreign_sites.extend(cfg.site_abbrev)

    log.info("Connecting to %s ...", db_url.split("@")[-1])
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()

        log.info("Counting active meters ...")
        active_counts = fetch_active_meter_counts(cur)

        log.info("Fetching monthly coverage (last %d months) ...", window_months)
        monthly_coverage = fetch_monthly_coverage(cur, window_months)

        log.info("Detecting zero-coverage meters ...")
        zero_meters = fetch_zero_coverage_meters(cur)

        log.info("Detecting stale meters (>%d days) ...", stale_days)
        stale_meters = fetch_stale_meters(cur, stale_days)

        log.info("Sampling last ingest per (site, source) ...")
        last_ingest = fetch_last_ingest_per_source(cur)

        log.info("Checking for cross-country leak (foreign sites in this DB) ...")
        cross_country = fetch_cross_country_meters(cur, country.upper(), foreign_sites)

    finally:
        conn.close()

    sites_with_data = set(monthly_coverage) | set(last_ingest)
    sites_with_active_meters = {s for s, n in active_counts.items() if n > 0}

    declared_missing = sorted(declared_sites - sites_with_data) if declared_sites else []
    orphans = sorted((sites_with_data - declared_sites) - {c["community"] for c in cross_country}) if declared_sites else []

    deficits = detect_monthly_deficits(
        monthly_coverage, deficit_threshold=deficit_threshold,
    )

    return {
        "country": country.upper(),
        "database_label": db_url.split("@")[-1],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_months": window_months,
        "stale_days": stale_days,
        "deficit_threshold": deficit_threshold,
        "active_counts": active_counts,
        "monthly_coverage": monthly_coverage,
        "monthly_deficits": deficits,
        "zero_coverage_meters": zero_meters,
        "zero_coverage_summary": summarize_zero_coverage(zero_meters, active_counts),
        "stale_meters": stale_meters,
        "last_ingest": last_ingest,
        "cross_country_meters": cross_country,
        "declared_sites_missing_data": declared_missing,
        "orphan_sites": orphans,
        "totals": {
            "active_meters": sum(active_counts.values()),
            "zero_coverage_meters": len(zero_meters),
            "stale_meters": len(stale_meters),
            "monthly_deficits_flagged": len(deficits),
            "sites_with_active_meters": len(sites_with_active_meters),
            "sites_with_data": len(sites_with_data),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="1PDB coverage gap audit (read-only).")
    p.add_argument("--country", "-c", default="LS", help="Country DB to audit (LS, BN). Default: LS")
    p.add_argument("--database-url", help="Override DATABASE_URL. Default: from env or per-country preset.")
    p.add_argument("--window-months", type=int, default=8, help="Months back for the coverage matrix. Default: 8")
    p.add_argument("--stale-days", type=int, default=30, help="Days for the stale-meter cutoff. Default: 30")
    p.add_argument("--deficit-threshold", type=float, default=0.50,
                   help="Flag months with rows < this fraction of baseline median. Default: 0.50")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    p.add_argument("--out", "-o", help="Write output to PATH instead of stdout.")
    p.add_argument(
        "--snapshot", action="store_true",
        help="Persist the audit to the coverage_snapshots table (requires the "
             "018 migration applied). Used by the systemd timer; safe to run "
             "manually too.",
    )
    p.add_argument(
        "--triggered-by", default="cli",
        help="Label to record in coverage_snapshots.triggered_by (e.g. 'timer', 'cli', 'admin:user_id').",
    )
    args = p.parse_args()

    db_url = _resolve_db_url(args.country, args.database_url)

    try:
        payload = run_audit(
            args.country, db_url,
            window_months=args.window_months,
            stale_days=args.stale_days,
            deficit_threshold=args.deficit_threshold,
        )
    except psycopg2.Error as e:
        log.error("Database error: %s", e)
        return 1

    text = json.dumps(payload, indent=2, default=str) if args.json else render_markdown(payload)

    if args.out:
        with open(args.out, "w") as fp:
            fp.write(text)
        log.info("Wrote %s (%d bytes)", args.out, len(text))
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    log.info(
        "Audit done: %d active meters, %d zero-coverage, %d stale, %d monthly deficits flagged",
        payload["totals"]["active_meters"],
        payload["totals"]["zero_coverage_meters"],
        payload["totals"]["stale_meters"],
        payload["totals"]["monthly_deficits_flagged"],
    )

    if args.snapshot:
        try:
            snap_id = _persist_snapshot(db_url, payload, triggered_by=args.triggered_by)
            log.info("Persisted snapshot id=%d (triggered_by=%s)", snap_id, args.triggered_by)
        except Exception as e:  # noqa: BLE001
            log.error("Snapshot persistence failed: %s", e)
            return 1

    return 0


def _persist_snapshot(db_url: str, payload: Dict[str, Any], *, triggered_by: str) -> int:
    """Insert a row into ``coverage_snapshots``. Mirrors the inline SQL used
    by ``acdb-api/coverage_audit.py``; kept here so the systemd timer
    doesn't depend on the FastAPI process being available.
    """
    import psycopg2.extras  # noqa: F401  (already imported at module top)
    totals = payload.get("totals", {})
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO coverage_snapshots (
                country_code,
                active_meters, zero_coverage_meters, stale_meters,
                monthly_deficits_flagged, sites_with_active_meters, sites_with_data,
                window_months, stale_days, deficit_threshold,
                monthly_coverage, monthly_deficits, last_ingest,
                zero_coverage_summary, cross_country_meters,
                declared_sites_missing, orphan_sites,
                zero_coverage_meters_detail, stale_meters_detail,
                triggered_by, notes
            ) VALUES (
                %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s
            ) RETURNING id
            """,
            (
                payload["country"],
                totals.get("active_meters", 0),
                totals.get("zero_coverage_meters", 0),
                totals.get("stale_meters", 0),
                totals.get("monthly_deficits_flagged", 0),
                totals.get("sites_with_active_meters", 0),
                totals.get("sites_with_data", 0),
                payload["window_months"], payload["stale_days"], payload["deficit_threshold"],
                json.dumps(payload.get("monthly_coverage", {})),
                json.dumps(payload.get("monthly_deficits", [])),
                json.dumps(payload.get("last_ingest", {})),
                json.dumps(payload.get("zero_coverage_summary", {})),
                json.dumps(payload.get("cross_country_meters", [])),
                json.dumps(payload.get("declared_sites_missing_data", [])),
                json.dumps(payload.get("orphan_sites", [])),
                json.dumps(payload.get("zero_coverage_meters", []), default=str),
                json.dumps(payload.get("stale_meters", []), default=str),
                triggered_by,
                None,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return int(new_id)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
