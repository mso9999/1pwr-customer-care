#!/usr/bin/env python3
"""
Audit Lesotho customer balances: compare 1PDB vs Koios (LS sites) and ThunderCloud (MAK/LAB).

Modes:
  (default)   Full audit report with per-account deltas
  --check     Quick drift check (exit 1 if any delta exceeds threshold)
  --reconcile Preview balance_seed rows for drift (dry run by default)
  --apply     With --reconcile, insert balance_seed transactions

Usage:
    PYTHONPATH=acdb-api python3 scripts/ops/audit_ls_balances.py
    PYTHONPATH=acdb-api python3 scripts/ops/audit_ls_balances.py --check
    PYTHONPATH=acdb-api python3 scripts/ops/audit_ls_balances.py --reconcile
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

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
OPS = ROOT / "scripts" / "ops"
for path in (ACDB_API, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from balance_engine import get_balance_kwh  # noqa: E402
from country_config import get_tariff_rate_for_site  # noqa: E402
from cutover_ls_common import is_bulk_excluded_account  # noqa: E402
from sparkmeter_customer import THUNDERCLOUD_SITES  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("audit_ls")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
TC_API_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
TC_AUTH_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")

DRIFT_THRESHOLD_KWH = 0.5
SITE_CODE_RE = re.compile(r"([A-Z]{2,4})$")
VALID_ACCOUNT_RE = re.compile(r"^\d{4}[A-Z]{2,4}$")


def _site_code(account_number: str) -> str:
    match = SITE_CODE_RE.search((account_number or "").upper())
    return match.group(1) if match else ""


def koios_login(session: requests.Session) -> None:
    email = os.environ.get("KOIOS_WEB_EMAIL", "")
    password = os.environ.get("KOIOS_WEB_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("KOIOS_WEB_EMAIL and KOIOS_WEB_PASSWORD are required")

    r = session.get(f"{KOIOS_BASE}/login", timeout=30)
    r.raise_for_status()
    csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not csrf:
        raise RuntimeError("Could not find CSRF token on Koios login page")
    r = session.post(
        f"{KOIOS_BASE}/login",
        data={"csrf_token": csrf.group(1), "email": email, "password": password},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Koios login failed: HTTP {r.status_code}")


def fetch_koios_balances() -> dict[str, tuple[float, float, float]]:
    """Return {account: (kwh, lsl, rate)} from Koios web API."""
    org_id = os.environ.get("KOIOS_ORG_ID", "")
    if not org_id:
        raise RuntimeError("KOIOS_ORG_ID is required")

    balances: dict[str, tuple[float, float, float]] = {}
    session = requests.Session()
    koios_login(session)

    page = 1
    while True:
        r = session.get(
            f"{KOIOS_BASE}/sm/organizations/{org_id}/customers",
            headers={"Accept": "application/json"},
            params={"page_size": 500, "page": page},
            timeout=120,
        )
        r.raise_for_status()
        customers = r.json().get("customers") or []
        if not customers:
            break

        for customer in customers:
            code = (customer.get("code") or "").strip().upper()
            if not code:
                continue
            site = _site_code(code)
            if site in THUNDERCLOUD_SITES:
                continue
            bal_obj = customer.get("balance") or {}
            try:
                credit_lsl = float(bal_obj.get("value") or 0)
            except (TypeError, ValueError):
                credit_lsl = 0.0
            rate = float(get_tariff_rate_for_site(site) or 0)
            kwh = round(credit_lsl / rate, 4) if rate > 0 else 0.0
            balances[code] = (kwh, credit_lsl, rate)

        log.info("Koios page %d: %d customers (running total %d)", page, len(customers), len(balances))
        if len(customers) < 500:
            break
        page += 1

    return balances


def fetch_thundercloud_balances() -> dict[str, tuple[float, float, float]]:
    """Return {account: (kwh, lsl, rate)} from ThunderCloud v0."""
    if not TC_AUTH_TOKEN:
        raise RuntimeError("TC_AUTH_TOKEN is required for MAK/LAB audit")

    r = requests.get(
        f"{TC_API_BASE}/api/v0/customers",
        params={"customers_only": "false", "reading_details": "false"},
        headers={"Authentication-Token": TC_AUTH_TOKEN},
        timeout=90,
    )
    r.raise_for_status()
    balances: dict[str, tuple[float, float, float]] = {}
    for customer in r.json().get("customers", []):
        code = (customer.get("code") or "").strip().upper()
        if not code:
            continue
        site = _site_code(code)
        if site not in THUNDERCLOUD_SITES:
            continue
        # ThunderCloud credit_balance is in CURRENCY (console labels it "Credit (ZAR)"),
        # not kWh — confirmed in prod (0302MAK API 39.5 == console 39.525 ZAR @ 5/kWh =
        # 7.9 kWh). Convert to kWh via the site tariff. (Prior code treated it as kWh and
        # over-stated MAK/LAB SM balances ~5x; see proactive-balance-freshness RCA.)
        try:
            credit_currency = float(customer.get("credit_balance") or 0)
        except (TypeError, ValueError):
            credit_currency = 0.0
        rate = float(get_tariff_rate_for_site(site) or 0)
        credit_kwh = round(credit_currency / rate, 4) if rate > 0 else 0.0
        balances[code] = (credit_kwh, round(credit_currency, 4), rate)
    return balances


def fetch_sparkmeter_balances() -> dict[str, tuple[float, float, float]]:
    koios = fetch_koios_balances()
    tc = fetch_thundercloud_balances()
    merged = dict(koios)
    for code, payload in tc.items():
        if code in merged:
            log.warning("Skipping Koios balance for %s; using ThunderCloud", code)
        merged[code] = payload
    return merged


def compute_1pdb_balances(conn) -> dict[str, float]:
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT account_number FROM accounts ORDER BY account_number")
    accounts = [row[0] for row in cur.fetchall()]
    cur.close()

    balances: dict[str, float] = {}
    for account in accounts:
        balance, _ = get_balance_kwh(conn, account)
        balances[account] = round(float(balance), 4)
    return balances


def run_audit(conn) -> list[tuple[str, float, float, float, str]]:
    log.info("Fetching SparkMeter balances (Koios + ThunderCloud)...")
    sm_balances = fetch_sparkmeter_balances()
    log.info("  %d SparkMeter accounts", len(sm_balances))

    log.info("Computing 1PDB balances...")
    pdb_balances = compute_1pdb_balances(conn)
    for account in sm_balances:
        if account not in pdb_balances:
            balance, _ = get_balance_kwh(conn, account)
            pdb_balances[account] = round(float(balance), 4)
    log.info("  %d 1PDB accounts", len(pdb_balances))

    all_accounts = sorted(set(sm_balances) | set(pdb_balances))
    results: list[tuple[str, float, float, float, str]] = []
    for account in all_accounts:
        sm_kwh, _, _ = sm_balances.get(account, (0.0, 0.0, 0.0))
        pdb_kwh = pdb_balances.get(account, 0.0)
        delta = round(sm_kwh - pdb_kwh, 4)
        site = _site_code(account)
        platform = "thundercloud" if site in THUNDERCLOUD_SITES else "koios"
        results.append((account, sm_kwh, pdb_kwh, delta, platform))
    return results


def print_report(results: list[tuple[str, float, float, float, str]], threshold: float) -> None:
    print()
    print("=" * 88)
    print("LS BALANCE AUDIT REPORT")
    print(f"  Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Threshold: {threshold:.2f} kWh")
    print(f"  Accounts: {len(results)}")
    print("=" * 88)

    drifted = [row for row in results if abs(row[3]) >= threshold]
    matched = len(results) - len(drifted)
    print(f"\n  Matched (|delta| < {threshold} kWh): {matched}")
    print(f"  Drifted (|delta| >= {threshold} kWh): {len(drifted)}")

    if not drifted:
        print()
        return

    print(
        f"\n{'Account':<12} {'Platform':<12} {'SM kWh':>10} {'1PDB kWh':>10} "
        f"{'Delta kWh':>10} {'Delta LSL':>10}"
    )
    print("-" * 88)
    total_delta = 0.0
    for account, sm_kwh, pdb_kwh, delta, platform in sorted(drifted, key=lambda row: -abs(row[3])):
        site = _site_code(account)
        rate = float(get_tariff_rate_for_site(site) or 0)
        total_delta += delta
        print(
            f"{account:<12} {platform:<12} {sm_kwh:>10.2f} {pdb_kwh:>10.2f} "
            f"{delta:>10.2f} {delta * rate:>10.2f}"
        )
    print("-" * 88)
    print(f"{'TOTAL':<12} {'':<12} {'':>10} {'':>10} {total_delta:>10.2f}")
    print()


def _seed_candidates(results, threshold, only_sites):
    out = []
    for row in results:
        account, _, _, delta, _ = row
        if abs(delta) < threshold:
            continue
        if is_bulk_excluded_account(account):
            continue
        if only_sites and _site_code(account) not in only_sites:
            continue
        out.append(row)
    return out


def preview_seeds(results, threshold: float, only_sites=None) -> int:
    seeds = _seed_candidates(results, threshold, only_sites)
    if not seeds:
        print("No balance seeds needed - all accounts within threshold.")
        return 0

    print(f"\n-- Balance seed preview ({len(seeds)} accounts)")
    print(f"-- Generated: {datetime.now(timezone.utc).isoformat()}")
    for account, _, _, delta, _ in seeds:
        rate = float(get_tariff_rate_for_site(_site_code(account)) or 0)
        lsl = round(delta * rate, 4) if rate > 0 else 0.0
        print(
            f"-- {account}: seed {delta:.4f} kWh ({lsl:.2f} LSL at {rate:.2f})"
        )
    print("\n-- To apply, re-run with: --reconcile --apply")
    return len(seeds)


def apply_seeds(conn, results, threshold: float, only_sites=None) -> int:
    cur = conn.cursor()
    ts = datetime.now(timezone.utc)
    count = 0
    skipped = 0
    seeds = _seed_candidates(results, threshold, only_sites)
    for account, _, _, delta, _ in seeds:
        rate = float(get_tariff_rate_for_site(_site_code(account)) or 0)
        amount = round(delta * rate, 4) if rate > 0 else 0.0
        cur.execute(
            """
            INSERT INTO transactions
                (account_number, meter_id, transaction_date, transaction_amount,
                 rate_used, kwh_value, is_payment, current_balance, source)
            VALUES (%s, '', %s, %s, %s, %s, true, 0, 'balance_seed')
            """,
            (account, ts, amount, rate, delta),
        )
        count += 1
    conn.commit()
    cur.close()
    if skipped:
        log.info("Skipped %d invalid account codes", skipped)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LS balances: 1PDB vs Koios/ThunderCloud")
    parser.add_argument("--check", action="store_true", help="Exit 1 when drift exceeds threshold")
    parser.add_argument("--reconcile", action="store_true", help="Preview or apply balance_seed rows")
    parser.add_argument("--apply", action="store_true", help="Insert seeds (requires --reconcile)")
    parser.add_argument("--threshold", type=float, default=DRIFT_THRESHOLD_KWH)
    parser.add_argument(
        "--only-sites",
        default="",
        help="Comma-separated site codes to limit --check/--reconcile/--apply to (e.g. MAK). "
        "Bulk-excluded accounts (LAB test, BVW, 0500MAK Power House, FAULTY, malformed) are "
        "always skipped.",
    )
    args = parser.parse_args()

    only_sites = {s.strip().upper() for s in args.only_sites.split(",") if s.strip()} or None

    if not DATABASE_URL:
        log.error("DATABASE_URL is required")
        return 1

    conn = psycopg2.connect(DATABASE_URL)
    try:
        results = run_audit(conn)
    except Exception as exc:
        log.error("Audit failed: %s", exc)
        conn.close()
        return 1

    threshold = args.threshold
    if args.check:
        drifted = [
            row for row in results
            if abs(row[3]) >= threshold and not is_bulk_excluded_account(row[0])
            and (not only_sites or _site_code(row[0]) in only_sites)
        ]
        if drifted:
            log.warning(
                "DRIFT DETECTED: %d accounts exceed %.2f kWh threshold",
                len(drifted),
                threshold,
            )
            for account, sm_kwh, pdb_kwh, delta, platform in sorted(
                drifted, key=lambda row: -abs(row[3])
            )[:15]:
                log.warning(
                    "  %s (%s): delta=%.2f kWh (SM=%.2f, 1PDB=%.2f)",
                    account,
                    platform,
                    delta,
                    sm_kwh,
                    pdb_kwh,
                )
            conn.close()
            return 1
        log.info("OK: all %d accounts within %.2f kWh threshold", len(results), threshold)
        conn.close()
        return 0

    print_report(results, threshold)

    if args.reconcile:
        if args.apply:
            log.info(
                "Applying balance seeds%s...",
                f" (sites={sorted(only_sites)})" if only_sites else "",
            )
            count = apply_seeds(conn, results, threshold, only_sites)
            log.info("Inserted %d balance_seed transactions", count)
            log.info("Verifying post-seed balances...")
            results2 = run_audit(conn)
            drifted2 = _seed_candidates(results2, threshold, only_sites)
            if drifted2:
                log.warning("POST-SEED: %d in-scope accounts still drifted", len(drifted2))
            else:
                log.info("POST-SEED: all in-scope accounts within threshold")
        else:
            preview_seeds(results, threshold, only_sites)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
