"""
SparkMeter ETL Worker
=====================

Scheduled batch pull from SparkMeter (Koios + ThunderCloud) that complements
the existing SMS-triggered ingestion in ``ingest.py``.

Pulls:
  - Tariff plans per site → ``sm_tariff_plans`` table
  - Customer accounts per site → updates ``customers`` / ``accounts`` with
    derived ``customer_type``
  - Transactions (incremental) → upserts ``transactions`` and rebuilds
    ``monthly_transactions`` aggregate

Multi-country support:
  LS sites → Koios v1 API (per-country credentials)
  BN sites → Koios v1 API (per-country credentials)
  MAK/LAB → ThunderCloud v0 API

Scheduling:
  Cron nightly (``0 2 * * *``).  Manual trigger via ``POST /api/etl/trigger``.

Environment variables (shared with sparkmeter_credit.py / sparkmeter_customer.py):
  KOIOS_BASE_URL                          — Koios base URL
  KOIOS_API_KEY / KOIOS_API_SECRET        — global read key
  KOIOS_API_KEY_XX / KOIOS_API_SECRET_XX  — per-country read key
  TC_API_BASE / TC_AUTH_TOKEN             — ThunderCloud auth
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional, Tuple

import requests
import psycopg2
import psycopg2.extras

from customer_api import get_connection

logger = logging.getLogger("cc-api.sm-etl")

API_TIMEOUT = 120

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")

TC_API_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")
TC_AUTH_TOKEN = os.environ.get("TC_AUTH_TOKEN", "")
TC_USERNAME = os.environ.get("THUNDERCLOUD_USERNAME", "")
TC_PASSWORD = os.environ.get("THUNDERCLOUD_PASSWORD", "")

THUNDERCLOUD_SITES = {"MAK", "LAB"}

_GLOBAL_KEY = os.environ.get("KOIOS_API_KEY", "")
_GLOBAL_SECRET = os.environ.get("KOIOS_API_SECRET", "")


def _build_site_country_map() -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    from country_config import _REGISTRY

    site_to_country: Dict[str, str] = {}
    for cc, cfg in _REGISTRY.items():
        for site in cfg.site_abbrev:
            site_to_country[site] = cc

    country_creds: Dict[str, Tuple[str, str]] = {}
    for cc in _REGISTRY:
        key = os.environ.get(f"KOIOS_API_KEY_{cc}", _GLOBAL_KEY)
        secret = os.environ.get(f"KOIOS_API_SECRET_{cc}", _GLOBAL_SECRET)
        country_creds[cc] = (key, secret)

    return site_to_country, country_creds


_site_to_country, _country_creds = _build_site_country_map()


def _koios_headers(site_code: str) -> dict:
    cc = _site_to_country.get(site_code, "LS")
    key, secret = _country_creds.get(cc, (_GLOBAL_KEY, _GLOBAL_SECRET))
    return {
        "Content-Type": "application/json",
        "X-API-KEY": key,
        "X-API-SECRET": secret,
    }


def _is_thundercloud(site_code: str) -> bool:
    return site_code in THUNDERCLOUD_SITES


# ---------------------------------------------------------------------------
# Customer type classifier
# ---------------------------------------------------------------------------

def classify_customer_type(
    tariff_plan_name: str,
    rate: Optional[float],
    is_business: bool,
    country_code: str = "LS",
) -> str:
    """Map SparkMeter tariff plan to HH / SME / C_I / UNK.

    Rules (per spec):
      - Rate <= standard prepaid + not business → HH
      - Rate <= standard prepaid + business flag → SME
      - Invoiced / rate > standard → C_I
      - Otherwise → UNK (flag for review)
    """
    from country_config import _REGISTRY

    cfg = _REGISTRY.get(country_code)
    standard_rate = cfg.default_tariff_rate if cfg else 5.0

    if rate is not None and rate > standard_rate:
        return "C_I"
    if is_business:
        return "SME"
    if rate is not None and rate <= standard_rate:
        return "HH"
    return "UNK"


def _resolve_override(conn, account_number: str) -> Optional[str]:
    """Check customer_type_overrides first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT customer_type FROM customer_type_overrides WHERE account_number = %s",
            (account_number,),
        )
        row = cur.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Koios v1 API pulls
# ---------------------------------------------------------------------------

def koios_pull_tariff_plans(site_code: str) -> List[Dict[str, Any]]:
    """GET /api/v1/tariff-plans for a Koios site."""
    try:
        r = requests.get(
            f"{KOIOS_BASE}/api/v1/tariff-plans",
            headers=_koios_headers(site_code),
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        plans = body.get("tariff_plans", body) if isinstance(body, dict) else body
        if not isinstance(plans, list):
            plans = []
        logger.info("Koios tariff plans for %s: %d plans", site_code, len(plans))
        return plans
    except Exception as e:
        logger.error("Koios tariff plans failed for %s: %s", site_code, e)
        return []


def koios_pull_accounts(site_code: str, page_size: int = 500) -> List[Dict[str, Any]]:
    """GET /api/v1/customers (paginated) for a Koios site."""
    all_customers: List[Dict[str, Any]] = []
    page = 1
    while True:
        try:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=_koios_headers(site_code),
                params={"page": page, "per_page": page_size},
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            body = r.json()
            customers = body.get("customers", body) if isinstance(body, dict) else body
            if not isinstance(customers, list) or len(customers) == 0:
                break
            all_customers.extend(customers)
            if len(customers) < page_size:
                break
            page += 1
        except Exception as e:
            logger.error("Koios accounts page %d failed for %s: %s", page, site_code, e)
            break
    logger.info("Koios accounts for %s: %d accounts", site_code, len(all_customers))
    return all_customers


def koios_pull_transactions(
    site_code: str, date_from: str, date_to: str, page_size: int = 500,
) -> List[Dict[str, Any]]:
    """GET /api/v1/transactions (paginated) for a Koios site."""
    all_txns: List[Dict[str, Any]] = []
    page = 1
    while True:
        try:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/transactions",
                headers=_koios_headers(site_code),
                params={
                    "page": page,
                    "per_page": page_size,
                    "start_date": date_from,
                    "end_date": date_to,
                },
                timeout=API_TIMEOUT,
            )
            r.raise_for_status()
            body = r.json()
            txns = body.get("transactions", body) if isinstance(body, dict) else body
            if not isinstance(txns, list) or len(txns) == 0:
                break
            all_txns.extend(txns)
            if len(txns) < page_size:
                break
            page += 1
        except Exception as e:
            logger.error("Koios transactions page %d failed for %s: %s", page, site_code, e)
            break
    logger.info(
        "Koios transactions for %s (%s to %s): %d txns",
        site_code, date_from, date_to, len(all_txns),
    )
    return all_txns


# ---------------------------------------------------------------------------
# ThunderCloud v0 API pulls
# ---------------------------------------------------------------------------

def _tc_get_token() -> Optional[str]:
    if TC_AUTH_TOKEN:
        return TC_AUTH_TOKEN
    if not TC_USERNAME or not TC_PASSWORD:
        return None
    try:
        r = requests.post(
            f"{TC_API_BASE}/login",
            data={"email": TC_USERNAME, "password": TC_PASSWORD},
            allow_redirects=False,
            timeout=API_TIMEOUT,
        )
        token = r.cookies.get("session") or r.cookies.get("remember_token")
        if token:
            return token
        auth_header = r.headers.get("Authentication-Token")
        if auth_header:
            return auth_header
    except Exception as e:
        logger.error("TC login failed: %s", e)
    return None


def tc_pull_tariff_plans(site_code: str) -> List[Dict[str, Any]]:
    token = _tc_get_token()
    if not token:
        logger.warning("TC: no token for %s, skipping tariff plans", site_code)
        return []
    try:
        r = requests.get(
            f"{TC_API_BASE}/api/v0/tariff-plans",
            headers={"Authentication-Token": token},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        plans = body.get("tariff_plans", body) if isinstance(body, dict) else body
        if not isinstance(plans, list):
            plans = []
        logger.info("TC tariff plans for %s: %d plans", site_code, len(plans))
        return plans
    except Exception as e:
        logger.error("TC tariff plans failed for %s: %s", site_code, e)
        return []


def tc_pull_accounts(site_code: str) -> List[Dict[str, Any]]:
    token = _tc_get_token()
    if not token:
        logger.warning("TC: no token for %s, skipping accounts", site_code)
        return []
    try:
        r = requests.get(
            f"{TC_API_BASE}/api/v0/customers",
            headers={"Authentication-Token": token},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        customers = body.get("customers", body) if isinstance(body, dict) else body
        if not isinstance(customers, list):
            customers = []
        logger.info("TC accounts for %s: %d accounts", site_code, len(customers))
        return customers
    except Exception as e:
        logger.error("TC accounts failed for %s: %s", site_code, e)
        return []


def tc_pull_transactions(
    site_code: str, date_from: str, date_to: str,
) -> List[Dict[str, Any]]:
    token = _tc_get_token()
    if not token:
        logger.warning("TC: no token for %s, skipping transactions", site_code)
        return []
    try:
        r = requests.get(
            f"{TC_API_BASE}/api/v0/transactions",
            headers={"Authentication-Token": token},
            params={"start_date": date_from, "end_date": date_to},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        txns = body.get("transactions", body) if isinstance(body, dict) else body
        if not isinstance(txns, list):
            txns = []
        logger.info(
            "TC transactions for %s (%s to %s): %d txns",
            site_code, date_from, date_to, len(txns),
        )
        return txns
    except Exception as e:
        logger.error("TC transactions failed for %s: %s", site_code, e)
        return []


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------

def _upsert_tariff_plans(conn, site_code: str, plans: List[Dict[str, Any]]):
    cc = _site_to_country.get(site_code, "LS")
    from country_config import _REGISTRY
    currency = _REGISTRY.get(cc, _REGISTRY["LS"]).currency

    with conn.cursor() as cur:
        for p in plans:
            plan_id = str(p.get("id", p.get("plan_id", "")))
            plan_name = str(p.get("name", p.get("plan_name", "")))
            rate = p.get("rate_amount") or p.get("rate")
            is_business = bool(p.get("is_business", p.get("business", False)))
            ctype = classify_customer_type(plan_name, float(rate) if rate else None, is_business, cc)
            cur.execute(
                """
                INSERT INTO sm_tariff_plans
                    (site_code, plan_id, plan_name, rate_amount, currency,
                     customer_type, is_business, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (site_code, plan_id) DO UPDATE SET
                    plan_name = EXCLUDED.plan_name,
                    rate_amount = EXCLUDED.rate_amount,
                    currency = EXCLUDED.currency,
                    customer_type = EXCLUDED.customer_type,
                    is_business = EXCLUDED.is_business,
                    synced_at = NOW()
                """,
                (site_code, plan_id, plan_name, rate, currency, ctype, is_business),
            )
    conn.commit()


def _upsert_accounts(conn, site_code: str, accounts: List[Dict[str, Any]]):
    cc = _site_to_country.get(site_code, "LS")
    with conn.cursor() as cur:
        for acct in accounts:
            account_number = str(acct.get("customer_code", acct.get("account_number", "")))
            if not account_number:
                continue

            override = _resolve_override(conn, account_number)
            if override:
                ctype = override
            else:
                tariff_name = str(acct.get("tariff_plan_name", acct.get("tariff_plan", "")))
                rate = acct.get("tariff_rate") or acct.get("rate")
                is_business = bool(acct.get("is_business", acct.get("business", False)))
                ctype = classify_customer_type(tariff_name, float(rate) if rate else None, is_business, cc)

            # Update customers table with customer_type if column exists
            cur.execute(
                """
                UPDATE customers SET customer_type = %s
                WHERE account_number = %s AND EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'customers' AND column_name = 'customer_type'
                )
                """,
                (ctype, account_number),
            )
    conn.commit()


def _upsert_transactions(conn, site_code: str, txns: List[Dict[str, Any]]):
    cc = _site_to_country.get(site_code, "LS")
    from country_config import _REGISTRY
    currency = _REGISTRY.get(cc, _REGISTRY["LS"]).currency

    with conn.cursor() as cur:
        for t in txns:
            txn_id = str(t.get("id", t.get("transaction_id", "")))
            account_number = str(t.get("customer_code", t.get("account_number", "")))
            if not account_number:
                continue
            amount = float(t.get("amount", t.get("transaction_amount", 0)) or 0)
            kwh = float(t.get("kwh", t.get("kwh_value", 0)) or 0)
            txn_date = t.get("created_at") or t.get("transaction_date") or t.get("timestamp")
            rate_used = float(t.get("rate_used", 0) or 0)

            cur.execute(
                """
                INSERT INTO transactions
                    (account_number, transaction_date, transaction_amount,
                     kwh_value, rate_used, source, site_code)
                VALUES (%s, %s, %s, %s, %s, 'sparkmeter_etl', %s)
                ON CONFLICT DO NOTHING
                """,
                (account_number, txn_date, amount, kwh, rate_used, site_code),
            )
    conn.commit()


# ---------------------------------------------------------------------------
# ETL orchestration
# ---------------------------------------------------------------------------

def _get_all_site_codes() -> List[str]:
    from country_config import ALL_SITE_ABBREV
    return list(ALL_SITE_ABBREV.keys())


def _get_last_sync_date(conn, site_code: str) -> str:
    """Get the last transaction date for a site from the transactions table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(transaction_date) FROM transactions WHERE site_code = %s",
            (site_code,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0].strftime("%Y-%m-%d")
    return "2020-01-01"


def pull_tariff_plans(site_code: str) -> List[Dict[str, Any]]:
    if _is_thundercloud(site_code):
        return tc_pull_tariff_plans(site_code)
    return koios_pull_tariff_plans(site_code)


def pull_accounts(site_code: str) -> List[Dict[str, Any]]:
    if _is_thundercloud(site_code):
        return tc_pull_accounts(site_code)
    return koios_pull_accounts(site_code)


def pull_transactions(site_code: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
    if _is_thundercloud(site_code):
        return tc_pull_transactions(site_code, date_from, date_to)
    return koios_pull_transactions(site_code, date_from, date_to)


def run_etl_cycle(
    sites: Optional[List[str]] = None,
    skip_transactions: bool = False,
) -> Dict[str, Any]:
    """Run a full ETL cycle: tariff plans + accounts + incremental transactions.

    Returns a summary dict with per-site results.
    """
    site_codes = sites or _get_all_site_codes()
    today = date.today().strftime("%Y-%m-%d")
    results: Dict[str, Any] = {"sites": {}, "started_at": datetime.now(timezone.utc).isoformat()}

    with get_connection() as conn:
        for site_code in sorted(site_codes):
            site_result: Dict[str, Any] = {"tariff_plans": 0, "accounts": 0, "transactions": 0, "errors": []}

            try:
                # 1. Tariff plans
                plans = pull_tariff_plans(site_code)
                if plans:
                    _upsert_tariff_plans(conn, site_code, plans)
                    site_result["tariff_plans"] = len(plans)

                # 2. Accounts
                accounts = pull_accounts(site_code)
                if accounts:
                    _upsert_accounts(conn, site_code, accounts)
                    site_result["accounts"] = len(accounts)

                # 3. Transactions (incremental)
                if not skip_transactions:
                    date_from = _get_last_sync_date(conn, site_code)
                    txns = pull_transactions(site_code, date_from, today)
                    if txns:
                        _upsert_transactions(conn, site_code, txns)
                        site_result["transactions"] = len(txns)

            except Exception as e:
                logger.error("ETL error for %s: %s", site_code, e)
                site_result["errors"].append(str(e))
                conn.rollback()

            results["sites"][site_code] = site_result
            logger.info("ETL %s: %s", site_code, site_result)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    return results


# ---------------------------------------------------------------------------
# FastAPI router — manual trigger + status
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Depends
from models import CurrentUser, CCRole
from middleware import require_employee, require_role

router = APIRouter(prefix="/api/etl", tags=["etl"])


@router.post("/trigger")
def trigger_etl(
    sites: Optional[List[str]] = None,
    skip_transactions: bool = False,
    user: CurrentUser = Depends(require_role(CCRole.superadmin, CCRole.onm_team)),
) -> Dict[str, Any]:
    """Manually trigger an ETL cycle.  Superadmin or O&M team only."""
    return run_etl_cycle(sites=sites, skip_transactions=skip_transactions)
