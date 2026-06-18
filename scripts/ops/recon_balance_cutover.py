#!/usr/bin/env python3
"""
recon_balance_cutover.py — One-time CC<->SparkMeter balance reconciliation cutover.

WHY THIS EXISTS (root-cause cure, not a band-aid)
-------------------------------------------------
CC never stores a balance; it reconstructs it from feeds:

    engine_balance = SUM(payment kWh) - SUM(consumption kWh) - legacy - balance_seed

The legacy ``balance_seed`` mechanism ran an audit (Koios - engine) and inserted a
plug row to force them equal. While the LS consumption feed was *under-counted*,
that injected large NEGATIVE plugs (~ -224k kWh). When the consumption feed was
later corrected (Koios dedup + gap backfill), those plugs were never removed, so
the engine became double-debited -> hundreds of accounts read falsely negative.

The credit feed, by contrast, is essentially complete (a full-window screen found
only 1 missing Koios credit out of 13,391). So the Koios>engine gap is NOT missing
payments. It is (a) the stale plugs and (b) *legitimate* pre-feed-window opening
balances (Koios remembers each customer's full lifetime ledger; CC's feed only
reaches back to ~mid-2025).

THE CURE
--------
1. Delete every ``balance_seed`` plug (the recurring band-aid).
2. Write ONE transparent ``opening_anchor`` per account, computed once from first
   principles AFTER both feeds were corrected:

       opening_anchor_kwh = Koios_authoritative_balance_kwh - engine_balance_without_plugs

   This makes the CC engine reconcile EXACTLY to the SparkMeter authoritative
   balance at cutover; the anchor cleanly represents the unreconstructable
   pre-window opening balance.
3. Going forward both feeds run correctly and drift is MONITORED (audit --check),
   never auto-plugged. Any future drift => a feed bug to investigate, not a plug.

Dry-run by default. ``--apply`` performs all work in a single transaction.
Re-runnable: it removes any prior ``opening_anchor`` rows before re-anchoring.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests

for _root in (Path("/opt/cc-portal/backend"), Path("/opt/cc-portal/backend/scripts/ops")):
    if _root.exists() and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from balance_engine import get_balance_kwh  # noqa: E402
from country_config import get_country, get_tariff_rate_for_site  # noqa: E402

try:
    from sparkmeter_customer import THUNDERCLOUD_SITES  # noqa: E402
except Exception:  # pragma: no cover
    THUNDERCLOUD_SITES = set()

try:
    from cutover_ls_common import is_bulk_excluded_account  # noqa: E402
except Exception:  # pragma: no cover
    def is_bulk_excluded_account(_account: str) -> bool:
        return False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("recon_cutover")

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
TC_API_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
SITE_RE = re.compile(r"([A-Z]{2,4})$")


def _site(account: str) -> str:
    m = SITE_RE.search((account or "").upper())
    return m.group(1) if m else ""


def _parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def koios_balances(country: str) -> dict[str, tuple[float, float]]:
    """Return {account: (balance_kwh, rate)} from Koios for *country*'s koios sites."""
    cc = country.upper()
    email = os.environ.get(f"KOIOS_WEB_EMAIL_{cc}") or os.environ.get("KOIOS_WEB_EMAIL", "")
    pw = os.environ.get(f"KOIOS_WEB_PASSWORD_{cc}") or os.environ.get("KOIOS_WEB_PASSWORD", "")
    if not email or not pw:
        raise RuntimeError(f"KOIOS_WEB_EMAIL_{cc}/KOIOS_WEB_PASSWORD_{cc} (or unsuffixed) required")
    cfg = get_country(cc)
    org = (
        os.environ.get(f"KOIOS_ORG_ID_{cc}")
        or cfg.koios_org_id
        or os.environ.get("KOIOS_ORG_ID", "")
    ).strip()
    if not org:
        raise RuntimeError(f"Koios org id for {cc} required")
    allowed = {s.upper() for s in cfg.site_abbrev.keys()}

    session = requests.Session()
    r = session.get(f"{KOIOS_BASE}/login", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not find Koios CSRF token")
    r = session.post(
        f"{KOIOS_BASE}/login",
        data={"csrf_token": m.group(1), "email": email, "password": pw},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Koios login failed: HTTP {r.status_code}")

    out: dict[str, tuple[float, float]] = {}
    page = 1
    while True:
        r = session.get(
            f"{KOIOS_BASE}/sm/organizations/{org}/customers",
            headers={"Accept": "application/json"},
            params={"page_size": 500, "page": page},
            timeout=120,
        )
        r.raise_for_status()
        customers = r.json().get("customers") or []
        if not customers:
            break
        for c in customers:
            code = (c.get("code") or "").strip().upper()
            if not code:
                continue
            site = _site(code)
            if allowed and site not in allowed:
                continue
            if site in THUNDERCLOUD_SITES:
                continue
            bal = c.get("balance") or {}
            try:
                val = float(bal.get("value") or 0)
            except (TypeError, ValueError):
                val = 0.0
            rate = float(get_tariff_rate_for_site(site) or 0)
            if rate <= 0:
                continue
            out[code] = (round(val / rate, 4), rate)
        log.info("Koios page %d: running total %d", page, len(out))
        if len(customers) < 500:
            break
        page += 1
    return out


def thundercloud_balances() -> dict[str, tuple[float, float]]:
    """Return {account: (balance_kwh, rate)} for ThunderCloud sites (LS MAK/LAB)."""
    token = os.environ.get("TC_AUTH_TOKEN", "")
    if not token:
        log.warning("TC_AUTH_TOKEN missing; skipping ThunderCloud balances")
        return {}
    r = requests.get(
        f"{TC_API_BASE}/api/v0/customers",
        params={"customers_only": "false", "reading_details": "false"},
        headers={"Authentication-Token": token},
        timeout=90,
    )
    r.raise_for_status()
    out: dict[str, tuple[float, float]] = {}
    for c in r.json().get("customers", []):
        code = (c.get("code") or "").strip().upper()
        if not code:
            continue
        site = _site(code)
        if site not in THUNDERCLOUD_SITES:
            continue
        try:
            cur = float(c.get("credit_balance") or 0)
        except (TypeError, ValueError):
            cur = 0.0
        rate = float(get_tariff_rate_for_site(site) or 0)
        if rate <= 0:
            continue
        out[code] = (round(cur / rate, 4), rate)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default="/opt/1pdb/.env")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--country", choices=["LS", "BN"], required=True)
    ap.add_argument("--anchor-source", default="opening_anchor")
    ap.add_argument("--tolerance", type=float, default=0.01)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dump", default="", help="Write per-account CSV (account,sm_kwh,engine_raw,anchor,rate)")
    args = ap.parse_args()

    vals = _parse_env_file(args.env_file)
    for k, v in vals.items():
        os.environ.setdefault(k, v)
    dsn = args.database_url or (
        vals.get("DATABASE_URL") if args.country == "LS" else vals.get("DATABASE_URL_BN")
    ) or ""
    if not dsn:
        raise SystemExit("Database DSN required (--database-url or env file)")

    log.info("Fetching SparkMeter authoritative balances (%s)...", args.country)
    sm = koios_balances(args.country)
    if args.country == "LS":
        sm.update(thundercloud_balances())
    log.info("  %d SparkMeter balances", len(sm))
    if not sm:
        raise SystemExit("No SparkMeter balances fetched; aborting (refusing to anchor blind)")

    ref_prefix = f"recon_cutover_{datetime.now(timezone.utc):%Y%m%d}"
    now = datetime.now(timezone.utc)
    st: dict[str, float] = {
        "seed_rows_before": 0, "deleted_seed": 0, "deleted_prior_anchor": 0,
        "accounts": 0, "anchored": 0, "skip_no_sm": 0, "skip_excluded": 0,
        "already_ok": 0, "pos_anchor": 0, "neg_anchor": 0,
        "sum_anchor_kwh": 0.0, "engine_neg_after": 0, "max_abs_anchor": 0.0,
    }
    rows_dump: list[tuple[str, float, float, float, float]] = []
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM transactions WHERE source = 'balance_seed'")
        st["seed_rows_before"] = int(cur.fetchone()[0])
        cur.execute("DELETE FROM transactions WHERE source = 'balance_seed'")
        st["deleted_seed"] = cur.rowcount
        cur.execute("DELETE FROM transactions WHERE source = %s", (args.anchor_source,))
        st["deleted_prior_anchor"] = cur.rowcount

        cur.execute("SELECT DISTINCT account_number FROM accounts ORDER BY account_number")
        accounts = [r[0] for r in cur.fetchall()]
        st["accounts"] = len(accounts)
        for acct in accounts:
            if is_bulk_excluded_account(acct):
                st["skip_excluded"] += 1
                continue
            info = sm.get((acct or "").strip().upper())
            if not info:
                st["skip_no_sm"] += 1
                continue
            sm_kwh, rate = info
            engine, _ = get_balance_kwh(conn, acct)
            anchor = round(sm_kwh - float(engine), 4)
            rows_dump.append((acct, sm_kwh, round(float(engine), 4), anchor, rate))
            if abs(anchor) <= args.tolerance:
                st["already_ok"] += 1
                continue
            cur.execute(
                "INSERT INTO transactions (account_number, meter_id, transaction_date, "
                "transaction_amount, rate_used, kwh_value, is_payment, current_balance, "
                "source, payment_reference) VALUES (%s,'',%s,%s,%s,%s,true,0,%s,%s)",
                (acct, now, round(anchor * rate, 4), rate, anchor, args.anchor_source,
                 f"{ref_prefix}_{acct}"),
            )
            st["anchored"] += 1
            st["sum_anchor_kwh"] += anchor
            st["max_abs_anchor"] = max(st["max_abs_anchor"], abs(anchor))
            if anchor > 0:
                st["pos_anchor"] += 1
            else:
                st["neg_anchor"] += 1
            if sm_kwh < 0:
                st["engine_neg_after"] += 1
        st["sum_anchor_kwh"] = round(st["sum_anchor_kwh"], 1)
        st["max_abs_anchor"] = round(st["max_abs_anchor"], 1)

        if args.apply:
            conn.commit()
            log.info("APPLIED and committed")
        else:
            conn.rollback()
            log.info("DRY-RUN: rolled back (no changes written)")
    finally:
        conn.close()

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            fh.write("account,sm_kwh,engine_raw,anchor,rate\n")
            for acct, smk, eng, anc, rate in sorted(rows_dump, key=lambda r: -abs(r[3])):
                fh.write(f"{acct},{smk},{eng},{anc},{rate}\n")
        log.info("Wrote per-account dump: %s", args.dump)

    log.info("Top 15 anchors by |kWh|:")
    for acct, smk, eng, anc, _rate in sorted(rows_dump, key=lambda r: -abs(r[3]))[:15]:
        log.info("  %-12s sm=%10.1f engine_raw=%10.1f anchor=%10.1f", acct, smk, eng, anc)

    print({"country": args.country, "mode": "apply" if args.apply else "dry_run", **st})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
