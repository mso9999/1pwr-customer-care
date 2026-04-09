"""
Audit BN customer balances: compare 1PDB computed balances against Koios live balances.

Modes:
  (default)   Full audit report with per-account deltas and reconciliation SQL
  --check     Quick drift check (exit 1 if any delta exceeds threshold)
  --reconcile Apply balance_seed transactions to zero out deltas (DRY RUN by default)
  --apply     Combined with --reconcile, actually INSERT the seeds

Usage:
    python3 audit_bn_balances.py                       # full report
    python3 audit_bn_balances.py --check               # monitoring mode
    python3 audit_bn_balances.py --reconcile            # preview seeds
    python3 audit_bn_balances.py --reconcile --apply    # apply seeds
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("audit_bn")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api:gKkYLkzYwSRPNoSwuC87YVqbzCmnhI4e@localhost:5432/onepower_bj",
)
KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
ORG_ID = "0123589c-7f1f-4eb4-8888-d8f8aa706ea4"

KOIOS_EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "mso@1pwrafrica.com")
KOIOS_PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "1PWRBN2026")

BN_TARIFF_RATE = 160.0

SITES = {
    "GBO": "a23c334e-33f7-473d-9ae3-9e631d5336e4",
    "SAM": "8f80b0a8-0502-4e26-9043-7152979360aa",
}

DRIFT_THRESHOLD_KWH = 0.5


def koios_login(session):
    r = session.get(f"{KOIOS_BASE}/login", timeout=30)
    r.raise_for_status()
    csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not csrf:
        raise RuntimeError("Could not find CSRF token on login page")
    r = session.post(
        f"{KOIOS_BASE}/login",
        data={"csrf_token": csrf.group(1), "email": KOIOS_EMAIL, "password": KOIOS_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Koios login failed: HTTP {r.status_code}")
    log.info("Koios web login successful")


def fetch_koios_balances(session):
    """Fetch all BN customer credit balances from Koios web session.

    Returns {account_code: balance_xof}.
    """
    balances = {}
    for site_code, site_id in sorted(SITES.items()):
        page = 0
        while True:
            r = session.get(
                f"{KOIOS_BASE}/sm/organizations/{ORG_ID}/customers",
                headers={"Accept": "application/json"},
                params={"page_size": 200, "site_id": site_id, "page": page},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            customers = data.get("customers", [])
            if not customers:
                break

            for c in customers:
                code = (c.get("code") or "").strip()
                if not code:
                    continue
                bal_obj = c.get("balance", {})
                try:
                    bal_xof = float(bal_obj.get("value", 0))
                except (ValueError, TypeError):
                    bal_xof = 0.0
                balances[code] = bal_xof

            total_count = data.get("total_count", 0)
            fetched = (page + 1) * 200
            log.info("  %s page %d: %d customers (total: %d)", site_code, page, len(customers), total_count)
            if fetched >= total_count or len(customers) < 200:
                break
            page += 1

    return balances


def compute_1pdb_balances(conn):
    """Compute kWh balances for all BN accounts using the balance engine logic.

    Returns {account_number: balance_kwh}.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT account_number,
               COALESCE(SUM(CASE WHEN is_payment THEN kwh_value ELSE 0 END), 0) as pay_kwh,
               COALESCE(SUM(CASE WHEN NOT is_payment THEN transaction_amount ELSE 0 END), 0) as leg_cons
        FROM transactions
        GROUP BY account_number
    """)
    txn_data = {}
    for acct, pay_kwh, leg_cons in cur.fetchall():
        txn_data[acct] = (float(pay_kwh), float(leg_cons))

    cur.execute("""
        SELECT account_number,
               COALESCE(SUM(hour_kwh), 0)
        FROM (
            SELECT account_number, reading_hour, MAX(kwh) AS hour_kwh
            FROM hourly_consumption
            GROUP BY account_number, reading_hour
        ) deduped
        GROUP BY account_number
    """)
    cons_data = {}
    for acct, kwh in cur.fetchall():
        cons_data[acct] = float(kwh)

    all_accounts = set(txn_data.keys()) | set(cons_data.keys())
    balances = {}
    for acct in all_accounts:
        pay_kwh, leg_cons = txn_data.get(acct, (0.0, 0.0))
        live_cons = cons_data.get(acct, 0.0)
        balances[acct] = round(pay_kwh - live_cons - leg_cons, 4)

    cur.close()
    return balances


def run_audit(conn, session):
    """Run full audit and return list of (account, koios_kwh, pdb_kwh, delta_kwh)."""
    log.info("Fetching Koios balances...")
    koios_xof = fetch_koios_balances(session)
    log.info("  %d accounts with Koios balances", len(koios_xof))

    log.info("Computing 1PDB balances...")
    pdb_kwh = compute_1pdb_balances(conn)
    log.info("  %d accounts with 1PDB data", len(pdb_kwh))

    all_accounts = sorted(set(koios_xof.keys()) | set(pdb_kwh.keys()))

    results = []
    for acct in all_accounts:
        k_xof = koios_xof.get(acct, 0.0)
        k_kwh = round(k_xof / BN_TARIFF_RATE, 4) if BN_TARIFF_RATE > 0 else 0.0
        p_kwh = pdb_kwh.get(acct, 0.0)
        delta = round(k_kwh - p_kwh, 4)
        results.append((acct, k_kwh, p_kwh, delta))

    return results


def print_report(results, threshold=DRIFT_THRESHOLD_KWH):
    """Print a formatted audit report."""
    print()
    print("=" * 80)
    print("BN BALANCE AUDIT REPORT")
    print(f"  Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Tariff rate: {BN_TARIFF_RATE} XOF/kWh")
    print(f"  Accounts: {len(results)}")
    print("=" * 80)

    drifted = [(a, k, p, d) for a, k, p, d in results if abs(d) >= threshold]
    matched = len(results) - len(drifted)

    print(f"\n  Matched (delta < {threshold} kWh): {matched}")
    print(f"  Drifted (delta >= {threshold} kWh): {len(drifted)}")

    if drifted:
        print(f"\n{'Account':<16} {'Koios kWh':>12} {'1PDB kWh':>12} {'Delta kWh':>12} {'Delta XOF':>12}")
        print("-" * 66)
        total_delta_kwh = 0.0
        for acct, k_kwh, p_kwh, delta in sorted(drifted, key=lambda x: -abs(x[3])):
            total_delta_kwh += delta
            print(f"{acct:<16} {k_kwh:>12.2f} {p_kwh:>12.2f} {delta:>12.2f} {delta * BN_TARIFF_RATE:>12.0f}")
        print("-" * 66)
        print(f"{'TOTAL':<16} {'':>12} {'':>12} {total_delta_kwh:>12.2f} {total_delta_kwh * BN_TARIFF_RATE:>12.0f}")

    print()


def generate_seed_sql(results, threshold=DRIFT_THRESHOLD_KWH):
    """Generate INSERT statements for balance_seed transactions."""
    seeds = []
    for acct, k_kwh, p_kwh, delta in results:
        if abs(delta) < threshold:
            continue
        seeds.append((acct, delta))

    if not seeds:
        print("No balance seeds needed - all accounts within threshold.")
        return seeds

    print(f"\n-- Balance seed transactions ({len(seeds)} accounts)")
    print(f"-- Generated: {datetime.now(timezone.utc).isoformat()}")
    for acct, delta_kwh in seeds:
        xof = round(delta_kwh * BN_TARIFF_RATE, 4)
        print(
            f"INSERT INTO transactions "
            f"(account_number, meter_id, transaction_date, transaction_amount, "
            f"rate_used, kwh_value, is_payment, current_balance, source) "
            f"VALUES ('{acct}', '', NOW(), {xof}, {BN_TARIFF_RATE}, {delta_kwh}, "
            f"true, 0, 'balance_seed');"
        )

    return seeds


VALID_ACCOUNT_RE = re.compile(r"^\d{4}(GBO|SAM)$")


def apply_seeds(conn, results, threshold=DRIFT_THRESHOLD_KWH):
    """Insert balance_seed transactions for drifted accounts."""
    cur = conn.cursor()
    ts = datetime.now(timezone.utc)
    count = 0
    skipped = 0
    for acct, k_kwh, p_kwh, delta in results:
        if abs(delta) < threshold:
            continue
        if not VALID_ACCOUNT_RE.match(acct):
            log.warning("  Skipping invalid account code: %s (delta=%.2f kWh)", acct, delta)
            skipped += 1
            continue
        xof = round(delta * BN_TARIFF_RATE, 4)
        cur.execute(
            """
            INSERT INTO transactions
                (account_number, meter_id, transaction_date, transaction_amount,
                 rate_used, kwh_value, is_payment, current_balance, source)
            VALUES (%s, '', %s, %s, %s, %s, true, 0, 'balance_seed')
            """,
            (acct, ts, xof, BN_TARIFF_RATE, delta),
        )
        count += 1
    conn.commit()
    cur.close()
    if skipped:
        log.info("  Skipped %d invalid account codes", skipped)
    return count


def main():
    parser = argparse.ArgumentParser(description="Audit BN balances: 1PDB vs Koios")
    parser.add_argument("--check", action="store_true", help="Quick drift check (exit 1 if drifted)")
    parser.add_argument("--reconcile", action="store_true", help="Generate/apply balance_seed transactions")
    parser.add_argument("--apply", action="store_true", help="Actually INSERT seeds (requires --reconcile)")
    parser.add_argument("--threshold", type=float, default=DRIFT_THRESHOLD_KWH, help="kWh drift threshold")
    args = parser.parse_args()

    threshold = args.threshold

    conn = psycopg2.connect(DATABASE_URL)
    session = requests.Session()
    log.info("Authenticating to Koios web UI...")
    koios_login(session)

    results = run_audit(conn, session)

    if args.check:
        drifted = [r for r in results if abs(r[3]) >= threshold]
        if drifted:
            log.warning("DRIFT DETECTED: %d accounts exceed %.2f kWh threshold", len(drifted), threshold)
            for acct, k, p, d in sorted(drifted, key=lambda x: -abs(x[3]))[:10]:
                log.warning("  %s: delta=%.2f kWh (Koios=%.2f, 1PDB=%.2f)", acct, d, k, p)
            conn.close()
            sys.exit(1)
        else:
            log.info("OK: all %d accounts within %.2f kWh threshold", len(results), threshold)
            conn.close()
            sys.exit(0)

    print_report(results, threshold)

    if args.reconcile:
        if args.apply:
            log.info("Applying balance seeds...")
            count = apply_seeds(conn, results, threshold)
            log.info("Inserted %d balance_seed transactions", count)

            log.info("Verifying post-seed balances...")
            results2 = run_audit(conn, session)
            drifted2 = [r for r in results2 if abs(r[3]) >= threshold]
            if drifted2:
                log.warning("POST-SEED: %d accounts still drifted", len(drifted2))
            else:
                log.info("POST-SEED: all accounts within threshold")
        else:
            generate_seed_sql(results, threshold)
            print("\n-- To apply, re-run with: --reconcile --apply")

    conn.close()
    session.close()


if __name__ == "__main__":
    main()
