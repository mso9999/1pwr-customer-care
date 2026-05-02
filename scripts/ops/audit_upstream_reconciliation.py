#!/usr/bin/env python3
"""
1PDB ↔ upstream (TC + Koios) reconciliation.

For each (site, month) cell where 1PDB looks deficient, classifies the
gap as one of:

  * **we_missed**   -- upstream has data, 1PDB doesn't ⇒ ingest gap, re-pull
  * **upstream_missing** -- upstream doesn't have it either ⇒ source-side gap
  * **match**       -- 1PDB matches upstream ⇒ false positive in our audit
  * **probe_failed**-- couldn't tell (network / auth / rate limit)

For MAK (TC parquets) the check is per-day -- TC stores one parquet per
day, so we directly compare which days have a parquet on the source vs
which days have hourly rows in our DB. For Koios sites it's
per-(site, month) using a single sampled day mid-month plus the freshness
endpoint -- enough to distinguish the three classes without burning the
30k-req/day Koios budget.

Reads credentials from the standard prod env (``/opt/1pdb/.env`` or
``/opt/cc-portal/backend/.env``):

  ``DATABASE_URL``, ``DATABASE_URL_BN``
  ``KOIOS_API_KEY``, ``KOIOS_API_SECRET``           (LS)
  ``KOIOS_API_KEY_BN``, ``KOIOS_API_SECRET_BN``     (BN)
  ``THUNDERCLOUD_USERNAME``, ``THUNDERCLOUD_PASSWORD`` (MAK)

Usage::

    # Run on production CC host
    sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; \\
        CC_BACKEND_DIR=/opt/cc-portal/backend \\
        /opt/cc-portal/backend/venv/bin/python3 \\
        /opt/cc-portal/backend/scripts/ops/audit_upstream_reconciliation.py \\
        --country LS --window-months 8 \\
        --out /tmp/upstream-recon-LS.md'

    # Limit to specific (site, month) pairs (matches deficit triage list)
    .../audit_upstream_reconciliation.py --country LS \\
        --cells MAS:2025-12 MAS:2026-01 KET:2026-01 KET:2026-03 \\
                LSB:2026-03 MAT:2026-03 MAT:2026-02 \\
        --skip-tc

    # MAK TC-only inventory (per-day)
    .../audit_upstream_reconciliation.py --country LS --skip-koios

Exit codes:
    0 -- run succeeded (regardless of how many gaps were classified)
    1 -- DB or auth error
    2 -- CLI / argument error
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import re
import sys
import time
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("upstream_recon")


# ---------------------------------------------------------------------------
# Helpers shared with the coverage audit
# ---------------------------------------------------------------------------

def _ensure_backend_on_path() -> None:
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


def _resolve_db_url(country: str) -> str:
    cc = country.strip().upper()
    if cc == "BN":
        return os.environ.get("DATABASE_URL_BN") or "postgresql://cc_api@localhost:5432/onepower_bj"
    return os.environ.get("DATABASE_URL", "postgresql://cc_api@localhost:5432/onepower_cc")


# ---------------------------------------------------------------------------
# Koios v2 ``data/historical`` -- per (site, sampled day) row count
# ---------------------------------------------------------------------------

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
KOIOS_TIMEOUT = 30
KOIOS_INTER_REQ_DELAY = 0.5  # be polite, the limit is 3 req/5s


def _koios_creds(country: str) -> Tuple[str, str]:
    cc = country.upper()
    key = os.environ.get(f"KOIOS_API_KEY_{cc}") or os.environ.get("KOIOS_API_KEY", "")
    secret = os.environ.get(f"KOIOS_API_SECRET_{cc}") or os.environ.get("KOIOS_API_SECRET", "")
    return key, secret


def _koios_headers(country: str) -> Dict[str, str]:
    key, secret = _koios_creds(country)
    return {"X-API-KEY": key, "X-API-SECRET": secret, "Content-Type": "application/json"}


def koios_count_for_day(country: str, org_id: str, site_uuid: str, day: str) -> int:
    """Best-effort row count for one site/day from Koios v2.

    Uses the ``per_page`` knob to cap the fetch size. Returns the raw row
    count for that day (this is what Koios *says* it has -- our DB should
    have at least this many rows for the same (community, day)).
    """
    url = f"{KOIOS_BASE}/api/v2/organizations/{org_id}/data/historical"
    body = {
        "filters": {"sites": [site_uuid], "date_range": {"from": day, "to": day}},
        "per_page": 1000,  # Koios v2 max is 1000; pagination handles the rest
    }
    total = 0
    cursor: Optional[str] = None
    page = 0
    while True:
        if cursor:
            body["cursor"] = cursor
        time.sleep(KOIOS_INTER_REQ_DELAY)
        try:
            r = requests.post(url, json=body, headers=_koios_headers(country), timeout=KOIOS_TIMEOUT)
        except requests.RequestException as e:
            raise RuntimeError(f"Koios HTTP error: {e}") from e
        if r.status_code == 429:
            raise RuntimeError("Koios 429 rate limit exhausted")
        if r.status_code != 200:
            raise RuntimeError(f"Koios HTTP {r.status_code}: {r.text[:200]}")
        body_json = r.json()
        data = body_json.get("data") or []
        total += len(data)
        page += 1
        cursor = body_json.get("next_cursor") or body_json.get("cursor")
        if not cursor or not data:
            break
        if page > 20:  # 20 pages × 5K = 100K rows is way more than expected for one day
            log.warning("Koios pagination overflow at site=%s day=%s, total=%d", site_uuid, day, total)
            break
    return total


def koios_freshness(country: str, org_id: str) -> Dict[str, str]:
    """Return ``{site_uuid: 'YYYY-MM-DD'}`` of the most recent reading per site."""
    url = f"{KOIOS_BASE}/api/v2/organizations/{org_id}/data/freshness"
    time.sleep(KOIOS_INTER_REQ_DELAY)
    r = requests.post(url, json={}, headers=_koios_headers(country), timeout=KOIOS_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Koios freshness HTTP {r.status_code}: {r.text[:200]}")
    fresh = r.json().get("freshness", {})
    out = {}
    for site_uuid, value in fresh.items():
        if value and isinstance(value, dict) and value.get("reading"):
            out[site_uuid] = value["reading"][:10]
    return out


# ---------------------------------------------------------------------------
# ThunderCloud ``/history/list.json`` -- per-day parquet inventory for MAK
# ---------------------------------------------------------------------------

TC_BASE = os.environ.get("THUNDERCLOUD_BASE_URL", "https://opl-location001.sparkmeter.cloud")


def tc_login() -> Optional[requests.Session]:
    session = requests.Session()
    user = os.environ.get("THUNDERCLOUD_USERNAME", "")
    pw = os.environ.get("THUNDERCLOUD_PASSWORD", "")
    if not user or not pw:
        log.warning("THUNDERCLOUD_USERNAME/PASSWORD not set; skipping TC inventory")
        return None
    try:
        first = session.get(f"{TC_BASE}/login", timeout=30, verify=False)
    except requests.RequestException as e:
        log.warning("TC login GET failed: %s", e)
        return None
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', first.text)
    if not m:
        log.warning("TC login: no CSRF token")
        return None
    second = session.post(
        f"{TC_BASE}/login",
        data={"csrf_token": m.group(1), "email": user, "password": pw},
        timeout=30, verify=False, allow_redirects=True,
    )
    if "/login" in second.url:
        log.warning("TC login failed -- check credentials")
        return None
    return session


def tc_list_files(session: requests.Session) -> List[str]:
    r = session.get(f"{TC_BASE}/history/list.json", timeout=60, verify=False)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") != "success":
        log.warning("TC list error: %s", payload.get("error"))
        return []
    return [f.get("filename", "") if isinstance(f, dict) else str(f)
            for f in payload.get("files", [])]


_TC_DATE_RE = re.compile(r"year=(\d{4})/month=(\d{2})/day=(\d{2})")


def tc_filename_to_date(filename: str) -> Optional[date]:
    m = _TC_DATE_RE.search(filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1PDB queries
# ---------------------------------------------------------------------------

def db_rows_per_day(cur, site: str, month_first: date, month_last_excl: date) -> Dict[str, int]:
    """Return ``{YYYY-MM-DD: row_count}`` for that site's hourly_consumption."""
    cur.execute(
        """
        SELECT (reading_hour AT TIME ZONE 'UTC')::date AS d, COUNT(*) AS n
          FROM hourly_consumption
         WHERE community = %s
           AND reading_hour >= %s::timestamp
           AND reading_hour <  %s::timestamp
         GROUP BY d
         ORDER BY d
        """,
        (site, month_first, month_last_excl),
    )
    return {r[0].isoformat(): int(r[1]) for r in cur.fetchall()}


def db_rows_per_site_month(cur, sites: List[str], months: List[Tuple[date, date]]) -> Dict[Tuple[str, str], int]:
    """Return ``{(site, YYYY-MM): row_count}`` for all (site, month) pairs."""
    if not sites or not months:
        return {}
    out: Dict[Tuple[str, str], int] = {}
    for site in sites:
        for first, last in months:
            cur.execute(
                "SELECT COUNT(*) FROM hourly_consumption "
                " WHERE community = %s AND reading_hour >= %s::timestamp AND reading_hour < %s::timestamp",
                (site, first, last),
            )
            out[(site, first.strftime("%Y-%m"))] = int(cur.fetchone()[0])
    return out


# ---------------------------------------------------------------------------
# Cell parsing + month math
# ---------------------------------------------------------------------------

def parse_cell(cell: str) -> Tuple[str, str]:
    """Parse 'SITE:YYYY-MM' into (site, 'YYYY-MM')."""
    if ":" not in cell:
        raise ValueError(f"Bad cell {cell!r}, expected SITE:YYYY-MM")
    site, ym = cell.split(":", 1)
    return site.strip().upper(), ym.strip()


def month_bounds(ym: str) -> Tuple[date, date]:
    """'YYYY-MM' -> (first_of_month, first_of_NEXT_month)."""
    y, m = (int(x) for x in ym.split("-"))
    first = date(y, m, 1)
    if m == 12:
        last = date(y + 1, 1, 1)
    else:
        last = date(y, m + 1, 1)
    return first, last


def sample_days(first: date, last_excl: date, n: int = 3) -> List[str]:
    """Return up to n evenly-spaced sample days within [first, last_excl)."""
    days = (last_excl - first).days
    if days <= 0:
        return []
    if days <= n:
        return [(first + timedelta(days=i)).isoformat() for i in range(days)]
    step = days / (n + 1)
    return [(first + timedelta(days=int(step * (i + 1)))).isoformat() for i in range(n)]


# ---------------------------------------------------------------------------
# Reconciliation orchestration
# ---------------------------------------------------------------------------

def classify_cell(db_rows: int, upstream_rows_sample: int, *, expected_per_day_baseline: int) -> str:
    """Heuristic classifier for one (site, month, sample_day)."""
    if upstream_rows_sample == 0:
        return "upstream_missing"
    # Per-day comparison: db_rows is for the WHOLE month (~30 days),
    # upstream_rows_sample is for ONE sample day. We approximate the
    # day's contribution as (db_rows / days_in_month).
    return "see_per_day"  # caller refines


def reconcile_koios_cell(
    country: str, org_id: str, site_code: str, site_uuid: str, ym: str,
    cur, freshness_iso: Optional[str],
) -> Dict[str, Any]:
    """Probe Koios for one (site, month) cell and classify."""
    first, last = month_bounds(ym)
    days = sample_days(first, last, n=3)
    db_per_day = db_rows_per_day(cur, site_code, first, last)
    db_month_total = sum(db_per_day.values())

    # Freshness short-circuit: if Koios has data only up to before this
    # month started, no point sampling -- it's upstream-missing.
    if freshness_iso:
        try:
            fresh_d = datetime.strptime(freshness_iso, "%Y-%m-%d").date()
            if fresh_d < first:
                return {
                    "site": site_code, "month": ym,
                    "db_rows": db_month_total,
                    "samples": [],
                    "koios_freshness": freshness_iso,
                    "verdict": "upstream_missing",
                    "note": f"Koios freshness {freshness_iso} predates {ym}",
                }
        except ValueError:
            pass

    samples: List[Dict[str, Any]] = []
    for d in days:
        db_count_day = db_per_day.get(d, 0)
        try:
            up = koios_count_for_day(country, org_id, site_uuid, d)
        except Exception as e:
            samples.append({"day": d, "db": db_count_day, "koios": None, "error": str(e)})
            continue
        samples.append({"day": d, "db": db_count_day, "koios": up})

    # Verdict from samples that did probe successfully. Per-day strict:
    #   * **match**          -- every sample day, db == koios (or both 0)
    #   * **we_missed**       -- every sample day, db == 0 < koios
    #   * **we_missed_partial** -- mixed (some days match, some we missed)
    #   * **we_have_extra**   -- weird, db > koios on some day (probably 1PDB
    #                            counts a different time-bucket; report so we
    #                            don't silently treat as a gap)
    sampled = [s for s in samples if s.get("koios") is not None]
    if not sampled:
        return {
            "site": site_code, "month": ym,
            "db_rows": db_month_total, "samples": samples,
            "koios_freshness": freshness_iso,
            "verdict": "probe_failed",
        }
    upstream_total = sum(s["koios"] for s in sampled)
    if upstream_total == 0:
        verdict = "upstream_missing"
    else:
        per_day = []
        for s in sampled:
            up = s["koios"]
            db = s["db"]
            if up == 0 and db == 0:
                per_day.append("both_empty")
            elif db == 0 and up > 0:
                per_day.append("we_missed")
            elif db >= up * 0.95:
                per_day.append("match")
            elif db > up * 1.05:
                per_day.append("we_have_extra")
            else:
                per_day.append("we_missed_partial")
        if all(d in ("match", "both_empty") for d in per_day):
            verdict = "match"
        elif all(d == "we_missed" for d in per_day):
            verdict = "we_missed"
        elif "we_have_extra" in per_day:
            verdict = "we_have_extra"
        else:
            verdict = "we_missed_partial"

    # List the specific missed days so ops can do a targeted re-pull.
    missed_days = [s["day"] for s in sampled if s.get("koios", 0) > 0 and s.get("db", 0) == 0]
    return {
        "site": site_code, "month": ym,
        "db_rows": db_month_total, "samples": samples,
        "koios_freshness": freshness_iso,
        "verdict": verdict,
        "missed_days": missed_days,
    }


def reconcile_tc_mak(cur, audit_window_first: date, audit_window_last_excl: date) -> Dict[str, Any]:
    """Per-day TC parquet inventory for MAK + comparison with hourly_consumption."""
    sess = tc_login()
    if sess is None:
        return {"verdict": "probe_failed", "reason": "tc_login_failed"}
    files = tc_list_files(sess)
    by_date_files: Dict[str, int] = defaultdict(int)
    for f in files:
        d = tc_filename_to_date(f)
        if d and audit_window_first <= d < audit_window_last_excl:
            by_date_files[d.isoformat()] += 1
    if not by_date_files:
        return {"verdict": "probe_failed", "reason": "no_files_in_window"}

    db_per_day = db_rows_per_day(cur, "MAK", audit_window_first, audit_window_last_excl)

    days_with_tc_no_db = []
    days_with_db_no_tc = []
    days_with_both = 0
    for day, n_files in sorted(by_date_files.items()):
        if db_per_day.get(day, 0) == 0:
            days_with_tc_no_db.append({"day": day, "tc_files": n_files})
        else:
            days_with_both += 1
    for day in sorted(db_per_day):
        if day not in by_date_files and db_per_day[day] > 0:
            days_with_db_no_tc.append({"day": day, "db_rows": db_per_day[day]})

    return {
        "tc_total_days": len(by_date_files),
        "db_total_days": len(db_per_day),
        "days_with_both": days_with_both,
        "days_with_tc_no_db": days_with_tc_no_db,    # ← THE ONES WE MISSED
        "days_with_db_no_tc": days_with_db_no_tc,    # rare: data without parquet
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def deficit_cells_from_audit(country: str, db_url: str, *, window_months: int, deficit_threshold: float) -> List[Tuple[str, str]]:
    """Re-run the coverage audit and return (site, YYYY-MM) cells flagged as
    complete-month deficits. Doesn't include in-progress current month.
    """
    _ensure_backend_on_path()
    sd = os.path.dirname(os.path.abspath(__file__))
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import audit_coverage_gaps as cov  # type: ignore[import-not-found]

    payload = cov.run_audit(country, db_url, window_months=window_months,
                            stale_days=30, deficit_threshold=deficit_threshold)
    cells: List[Tuple[str, str]] = []
    for d in payload["monthly_deficits"]:
        if not d.get("in_progress"):
            cells.append((d["site"], d["month"]))
    return cells


_REPULL_RECIPES = {
    "LS": (
        "/opt/1pdb/services/import_hourly.py {first} {last} --country LS --site {site} --no-skip --no-aggregate"
    ),
    # BN uses a different script (web-session scrape) + DATABASE_URL_BN
    "BN": (
        "DATABASE_URL=$DATABASE_URL_BN /opt/cc-portal/backend/venv/bin/python3 "
        "/opt/1pdb/services/import_hourly_bn.py {first} {last} --site {site} --no-skip --no-aggregate"
    ),
}


def render_markdown(country: str, results: Dict[str, Any], tc_result: Optional[Dict[str, Any]]) -> str:
    out: List[str] = []
    out.append(f"# 1PDB ↔ upstream reconciliation -- {country} ({datetime.now(timezone.utc).isoformat()})")
    out.append("")
    out.append("Per (site, month) classification of the gap:")
    out.append("")
    out.append("* **we_missed** -- Koios has data, 1PDB doesn't. Re-pull via `import_hourly.py`.")
    out.append("* **we_missed_partial** -- 1PDB has < 50% of upstream samples. Likely partial ingest -- re-pull also recommended.")
    out.append("* **upstream_missing** -- Koios has no data either. Source-side gap; document and move on.")
    out.append("* **match** -- 1PDB ≈ upstream. False positive in the original audit; downstream report logic likely needs investigation.")
    out.append("* **probe_failed** -- couldn't tell (network, auth, rate limit).")
    out.append("")

    out.append("## Summary")
    out.append("")
    counts: Dict[str, int] = defaultdict(int)
    for r in results.values():
        counts[r["verdict"]] += 1
    out.append("| Verdict | Count |")
    out.append("|---|---|")
    for verdict in ("we_missed", "we_missed_partial", "upstream_missing", "match", "probe_failed"):
        out.append(f"| {verdict} | {counts.get(verdict, 0)} |")
    out.append("")

    out.append("## Per-(site, month) findings")
    out.append("")
    out.append("| Site | Month | Verdict | DB rows (month) | Sample days (DB / Koios) | Missed sample days | Note |")
    out.append("|---|---|---|---|---|---|---|")
    for key in sorted(results):
        r = results[key]
        sample_str = ", ".join(
            f"{s['day'][-2:]}: {s['db']:,}/{s['koios'] if s.get('koios') is not None else '?' }"
            for s in r.get("samples", [])
        )
        missed_str = ", ".join(d[-2:] for d in r.get("missed_days", [])) or "--"
        note = r.get("note", "")
        if any("error" in s for s in r.get("samples", [])):
            errs = "; ".join(s.get("error", "") for s in r["samples"] if "error" in s)
            note = (note + " | " + errs).strip(" |")[:200]
        out.append(
            f"| {r['site']} | {r['month']} | **{r['verdict']}** | {r['db_rows']:,} "
            f"| {sample_str} | {missed_str} | {note} |"
        )
    out.append("")

    # Re-pull recipe section. ``import_hourly.py`` CLI takes positional
    # ``from_date to_date`` args. ``--no-skip`` forces re-fetch (the
    # staleness check otherwise skips already-ingested days even though
    # the row count was below baseline). ``--site`` scopes the re-pull
    # so we don't accidentally hit Koios's 30k-req/day budget.
    we_missed_full = sorted(k for k, r in results.items() if r["verdict"] in ("we_missed", "we_missed_partial"))
    if we_missed_full:
        out.append("## Re-pull recipe")
        out.append("")
        out.append(
            "On the production CC host, for each `we_missed` / `we_missed_partial` cell. "
            "**Run one at a time** and check the journal (`journalctl -u 1pdb-consumption.service` "
            "or just watch stdout) -- Koios has a 30k req/day per-org budget, "
            "and a single full month re-pull for one site is ~1500 calls."
        )
        out.append("")
        recipe_template = _REPULL_RECIPES.get(country, _REPULL_RECIPES["LS"])
        # LS recipe uses the venv python via the LS shebang (bare); BN one
        # already includes its own python invocation in the template.
        cc_is_bn = (country == "BN")
        out.append("```bash")
        for key in we_missed_full:
            r = results[key]
            site, ym = r["site"], r["month"]
            first, last = month_bounds(ym)
            last_inclusive = (last - timedelta(days=1)).isoformat()
            out.append(
                f"# {key} ({r['verdict']})  -- missed sample days: "
                f"{', '.join(r.get('missed_days', [])) or '(none in sample)'}"
            )
            inner = recipe_template.format(
                first=first.isoformat(), last=last_inclusive, site=site,
            )
            if cc_is_bn:
                # The BN template already inlines DATABASE_URL_BN + python.
                line = f"sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; {inner}'"
            else:
                line = (
                    f"sudo bash -c 'set -a; source /opt/1pdb/.env; set +a; "
                    f"/opt/cc-portal/backend/venv/bin/python3 {inner}'"
                )
            out.append(line)
            out.append("")
        out.append("```")
        out.append("")
        out.append("After the re-pulls, re-run this reconciliation to confirm verdicts flip to `match`.")
        out.append("Then run `python3 /opt/cc-portal/backend/scripts/ops/audit_coverage_gaps.py "
                   "--country LS` to confirm the deficit count drops.")
        out.append("")

    if tc_result:
        out.append("## ThunderCloud parquet inventory (MAK)")
        out.append("")
        if tc_result.get("verdict") == "probe_failed":
            out.append(f"_TC probe failed: {tc_result.get('reason', '?')}_")
        else:
            out.append(f"* TC has parquets for **{tc_result['tc_total_days']}** days in the audit window.")
            out.append(f"* 1PDB has hourly rows for **{tc_result['db_total_days']}** days.")
            out.append(f"* Days covered in both: **{tc_result['days_with_both']}**.")
            out.append("")
            tn = tc_result.get("days_with_tc_no_db", [])
            if tn:
                out.append(f"### Days with TC parquet but **no** rows in 1PDB ({len(tn)})")
                out.append("")
                out.append("| Day | TC parquet files |")
                out.append("|---|---|")
                for d in tn:
                    out.append(f"| {d['day']} | {d['tc_files']} |")
                out.append("")
            else:
                out.append("**No days where TC has data and 1PDB doesn't.** ThunderCloud → 1PDB pipe is faithful in this window.")
                out.append("")
            dn = tc_result.get("days_with_db_no_tc", [])
            if dn:
                out.append(f"### Days with 1PDB rows but no TC parquet ({len(dn)})")
                out.append("Likely from another source (Koios early-MAK history) or post-deletion of TC parquets.")
                out.append("")
        out.append("")

    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Reconcile 1PDB against TC + Koios upstream.")
    p.add_argument("--country", "-c", default="LS")
    p.add_argument("--database-url")
    p.add_argument("--window-months", type=int, default=8)
    p.add_argument("--deficit-threshold", type=float, default=0.50)
    p.add_argument(
        "--cells", nargs="+", default=None,
        help="Limit to specific SITE:YYYY-MM cells (otherwise auto-discover deficits).",
    )
    p.add_argument("--skip-koios", action="store_true")
    p.add_argument("--skip-tc", action="store_true")
    p.add_argument("--out", "-o")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    _ensure_backend_on_path()
    try:
        from country_config import _REGISTRY  # type: ignore[attr-defined]
    except Exception as e:
        log.error("Cannot import country_config: %s", e)
        return 1

    cc = args.country.strip().upper()
    cfg = _REGISTRY.get(cc)
    if cfg is None:
        log.error("Unknown country %s", cc)
        return 2
    if not cfg.koios_org_id:
        log.warning("Country %s has no koios_org_id; --skip-koios is implicit.", cc)
        args.skip_koios = True

    db_url = args.database_url or _resolve_db_url(cc)

    # Resolve cells
    if args.cells:
        cells = [parse_cell(c) for c in args.cells]
    else:
        log.info("Discovering deficit cells from coverage audit ...")
        cells = deficit_cells_from_audit(cc, db_url,
                                         window_months=args.window_months,
                                         deficit_threshold=args.deficit_threshold)
    log.info("Reconciling %d (site, month) cells", len(cells))

    # Resolve site UUIDs (Koios sites only)
    site_uuid: Dict[str, str] = dict(cfg.koios_sites)

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        results: Dict[str, Any] = {}

        # Fetch freshness once per country (1 call instead of N)
        freshness: Dict[str, str] = {}
        if not args.skip_koios:
            try:
                log.info("Fetching Koios freshness for %s ...", cc)
                freshness = koios_freshness(cc, cfg.koios_org_id)
            except Exception as e:
                log.warning("Koios freshness failed: %s", e)

        # Reconcile each cell against Koios where applicable
        if not args.skip_koios:
            for site, ym in cells:
                key = f"{site}:{ym}"
                if site == "MAK":
                    # MAK doesn't go through Koios for current data -- skip the
                    # Koios reconcile and rely on TC inventory below.
                    continue
                uuid_ = site_uuid.get(site)
                if not uuid_:
                    results[key] = {"site": site, "month": ym, "db_rows": 0,
                                    "samples": [], "koios_freshness": None,
                                    "verdict": "probe_failed",
                                    "note": f"No Koios UUID for {site} in country_config"}
                    continue
                fresh = freshness.get(uuid_)
                log.info("[%s/%d] Reconciling %s ...", "K", len(cells), key)
                try:
                    results[key] = reconcile_koios_cell(
                        cc, cfg.koios_org_id, site, uuid_, ym, cur, fresh,
                    )
                except Exception as e:
                    log.warning("Reconcile failed for %s: %s", key, e)
                    results[key] = {"site": site, "month": ym, "db_rows": 0,
                                    "samples": [], "koios_freshness": fresh,
                                    "verdict": "probe_failed", "note": str(e)}

        # MAK TC inventory
        tc_result: Optional[Dict[str, Any]] = None
        if not args.skip_tc and cc == "LS" and "MAK" in cfg.site_abbrev:
            log.info("Probing TC parquet inventory for MAK ...")
            today = date.today()
            window_first = today.replace(day=1) - timedelta(days=args.window_months * 31)
            window_first = window_first.replace(day=1)
            tc_result = reconcile_tc_mak(cur, window_first, today)
    finally:
        conn.close()

    if args.json:
        text = json.dumps({"country": cc, "results": results, "tc_mak": tc_result},
                          indent=2, default=str)
    else:
        text = render_markdown(cc, results, tc_result)

    if args.out:
        with open(args.out, "w") as fp:
            fp.write(text)
        log.info("Wrote %s (%d bytes)", args.out, len(text))
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    counts = defaultdict(int)
    for r in results.values():
        counts[r["verdict"]] += 1
    log.info("Reconcile done: %s", dict(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
