"""
Proactive balance freshness — live SparkMeter balance cache + display resolver.

The ~1-day Koios/ThunderCloud readings batch means a customer whose meter just hit
zero can check their balance and still see yesterday's positive number. This module
pulls the meter's CURRENT credit (Koios v1 / ThunderCloud v0 via
``sparkmeter_credit.lookup_sm_balance``), caches it in ``account_balance_live``, and
exposes a display resolver that prefers the fresh value with a safe fallback to the
canonical ``balance_engine`` computation.

Design notes / RCA-driven decisions:

* **Display value = the SM meter balance (kWh).** The intended reconciliation
  ``live = CC_credits - koios_consumption`` needs Koios *lifetime* credits, which the
  ``/api/v1/customers`` endpoint does not return — it returns the current balance
  directly. When CC and Koios credits agree (every payment is pushed to SparkMeter)
  that formula reduces to exactly the Koios balance. The meter only honours its own
  balance anyway, so showing it is the honest answer to "how much power do I have?".
  ``cc_balance_kwh`` is stored alongside for drift visibility.
* **Never block a balance check.** SparkMeter lookups use a short timeout and any
  failure falls back to the cache (flagged ``stale``) or to ``balance_engine``.
* **TTL-gated.** Within ``balance_live_ttl_s`` an activity-triggered read reuses the
  cached value, so activity + scheduled pulls dedupe and we stay inside the Koios
  daily request budget.
* **Transaction isolation.** The cache upsert uses its own pooled connection (or a
  caller-supplied ``write_conn``) so it never commits or pollutes the caller's
  transaction (important on the payment-ingest path).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cc-api.balance-live")

DEFAULT_TTL_S = 600

# Sources that mean we have a genuine live SM reading (vs the engine fallback).
_LIVE_SOURCES = ("koios", "thundercloud")


def _read_int_config(conn, key: str, fallback: int) -> int:
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
        row = cur.fetchone()
        if row and row[0] is not None and str(row[0]).strip() != "":
            return int(float(row[0]))
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return fallback


def _read_ttl(conn) -> int:
    return max(0, _read_int_config(conn, "balance_live_ttl_s", DEFAULT_TTL_S))


def _tariff_rate(conn, account_number: str) -> float:
    """Currency/kWh for an account (mirrors payments._get_tariff_rate, no import cycle)."""
    from country_config import get_tariff_rate_for_site, COUNTRY
    import re

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT community FROM meters WHERE account_number = %s AND status = 'active' LIMIT 1",
            (account_number,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return get_tariff_rate_for_site(row[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Derive site from the account suffix (e.g. ...MAS) when no active meter row.
    m = re.search(r"([A-Z]{3})$", (account_number or "").upper())
    if m:
        rate = get_tariff_rate_for_site(m.group(1))
        if rate:
            return rate

    try:
        cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate' LIMIT 1")
        row = cur.fetchone()
        if row and row[0]:
            return float(row[0])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return COUNTRY.default_tariff_rate


_CACHE_COLUMNS = (
    "account_number",
    "live_balance_kwh",
    "sm_balance_kwh",
    "sm_balance_currency",
    "cc_balance_kwh",
    "source",
    "as_of",
    "stale",
    "last_error",
    "updated_at",
)


def _read_cache(conn, account_number: str) -> Optional[dict]:
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT account_number, live_balance_kwh, sm_balance_kwh, sm_balance_currency,
                   cc_balance_kwh, source, as_of, stale, last_error, updated_at
            FROM account_balance_live
            WHERE account_number = %s
            """,
            (account_number,),
        )
        row = cur.fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if not row:
        return None
    return dict(zip(_CACHE_COLUMNS, row))


def _upsert_cache(record: dict, *, write_conn=None) -> None:
    sql = """
        INSERT INTO account_balance_live
            (account_number, live_balance_kwh, sm_balance_kwh, sm_balance_currency,
             cc_balance_kwh, source, as_of, stale, last_error, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (account_number) DO UPDATE SET
            live_balance_kwh    = EXCLUDED.live_balance_kwh,
            sm_balance_kwh      = EXCLUDED.sm_balance_kwh,
            sm_balance_currency = EXCLUDED.sm_balance_currency,
            cc_balance_kwh      = EXCLUDED.cc_balance_kwh,
            source              = EXCLUDED.source,
            as_of               = EXCLUDED.as_of,
            stale               = EXCLUDED.stale,
            last_error          = EXCLUDED.last_error,
            updated_at          = NOW()
    """
    params = (
        record["account_number"],
        record.get("live_balance_kwh"),
        record.get("sm_balance_kwh"),
        record.get("sm_balance_currency"),
        record.get("cc_balance_kwh"),
        record.get("source", "engine"),
        record.get("as_of"),
        bool(record.get("stale", False)),
        record.get("last_error"),
    )
    if write_conn is not None:
        cur = write_conn.cursor()
        cur.execute(sql, params)
        return
    try:
        from customer_api import get_connection

        with get_connection() as wc:
            cur = wc.cursor()
            cur.execute(sql, params)
            wc.commit()
    except Exception as e:  # cache is best-effort; never break the caller
        logger.warning("balance_live cache upsert failed for %s: %s", record.get("account_number"), e)


def refresh_balance_live(
    conn,
    account_number: str,
    *,
    force: bool = False,
    max_age_s: Optional[int] = None,
    write_conn=None,
) -> dict:
    """Return a fresh (or TTL-cached) live-balance record for *account_number*.

    Cache hit within the TTL (and not stale) returns immediately with no network
    call. Otherwise pulls SparkMeter, reconciles, and upserts the cache. Always
    returns a record dict; never raises for lookup/cache failures (falls back to the
    last cached value or the engine balance).
    """
    from balance_engine import get_balance_kwh
    from sparkmeter_credit import lookup_sm_balance

    account_number = (account_number or "").strip().upper()
    now = datetime.now(timezone.utc)
    ttl = max_age_s if max_age_s is not None else _read_ttl(conn)
    cached = _read_cache(conn, account_number)

    if cached and not force:
        as_of = cached.get("as_of")
        updated_at = cached.get("updated_at")
        is_live = cached.get("source") in _LIVE_SOURCES
        if is_live and not cached.get("stale") and as_of and (now - as_of).total_seconds() <= ttl:
            return cached
        # Recently attempted but failing — don't hammer SparkMeter on every read.
        if cached.get("stale") and updated_at and (now - updated_at).total_seconds() <= ttl:
            return cached

    rate = _tariff_rate(conn, account_number)
    try:
        cc_balance, _ = get_balance_kwh(conn, account_number)
        cc_balance = round(float(cc_balance), 4)
    except Exception as e:
        logger.warning("balance_live: engine balance failed for %s: %s", account_number, e)
        try:
            conn.rollback()
        except Exception:
            pass
        cc_balance = cached.get("cc_balance_kwh") if cached else None

    look = None
    try:
        look = lookup_sm_balance(account_number, rate=rate)
    except Exception as e:
        logger.warning("balance_live: SM lookup raised for %s: %s", account_number, e)

    if look and look.get("balance_kwh") is not None:
        sm_kwh = round(float(look["balance_kwh"]), 4)
        record = {
            "account_number": account_number,
            "live_balance_kwh": sm_kwh,
            "sm_balance_kwh": sm_kwh,
            "sm_balance_currency": look.get("balance_currency"),
            "cc_balance_kwh": cc_balance,
            "source": look.get("source", "koios"),
            "as_of": now,
            "stale": False,
            "last_error": None,
        }
    elif cached and cached.get("sm_balance_kwh") is not None:
        # Lookup failed but we have a prior live reading: serve it, flagged stale.
        record = dict(cached)
        record["cc_balance_kwh"] = cc_balance
        record["stale"] = True
        record["last_error"] = "live lookup failed"
    else:
        # No live data ever — record an engine snapshot so the scheduler can reason.
        record = {
            "account_number": account_number,
            "live_balance_kwh": cc_balance,
            "sm_balance_kwh": None,
            "sm_balance_currency": None,
            "cc_balance_kwh": cc_balance,
            "source": "engine",
            "as_of": None,
            "stale": False,
            "last_error": "no live data",
        }

    _upsert_cache(record, write_conn=write_conn)
    return record


def get_display_balance_detail(
    conn,
    account_number: str,
    *,
    refresh: bool = True,
    max_age_s: Optional[int] = None,
) -> dict:
    """Resolve the balance to show a customer: fresh SM value if available, else engine.

    Returns ``{balance_kwh, as_of, source, live_source, stale, cc_balance_kwh}`` where
    ``source`` is ``'live'`` / ``'live_stale'`` / ``'engine'``.
    """
    from balance_engine import get_balance_kwh

    account_number = (account_number or "").strip().upper()

    rec = None
    if refresh:
        try:
            rec = refresh_balance_live(conn, account_number, max_age_s=max_age_s)
        except Exception as e:
            logger.warning("balance_live: refresh failed for %s: %s", account_number, e)
            rec = None
    if rec is None:
        rec = _read_cache(conn, account_number)

    if rec and rec.get("source") in _LIVE_SOURCES and rec.get("live_balance_kwh") is not None:
        return {
            "balance_kwh": round(float(rec["live_balance_kwh"]), 4),
            "as_of": rec.get("as_of"),
            "source": "live_stale" if rec.get("stale") else "live",
            "live_source": rec.get("source"),
            "stale": bool(rec.get("stale")),
            "cc_balance_kwh": rec.get("cc_balance_kwh"),
        }

    balance, as_of = get_balance_kwh(conn, account_number)
    return {
        "balance_kwh": round(float(balance), 4),
        "as_of": as_of,
        "source": "engine",
        "live_source": None,
        "stale": False,
        "cc_balance_kwh": round(float(balance), 4),
    }


def mark_account_due(account_number: str, *, write_conn=None) -> None:
    """Best-effort: flag an account for an immediate tiered-scheduler pull.

    Sets ``balance_refresh_state.next_due_at = now`` so the next scheduler tick
    refreshes this account (used right after a payment or a ticket touch, where the
    balance is likely to have changed). No-op if the table isn't present yet; never
    raises (callers are on request / background paths that must not break).
    """
    account_number = (account_number or "").strip().upper()
    if not account_number:
        return
    sql = """
        INSERT INTO balance_refresh_state (account_number, next_due_at, updated_at)
        VALUES (%s, NOW(), NOW())
        ON CONFLICT (account_number) DO UPDATE SET next_due_at = NOW(), updated_at = NOW()
    """
    if write_conn is not None:
        try:
            cur = write_conn.cursor()
            cur.execute(sql, (account_number,))
        except Exception as e:
            logger.warning("mark_account_due (write_conn) failed for %s: %s", account_number, e)
        return
    try:
        from customer_api import get_connection

        with get_connection() as wc:
            cur = wc.cursor()
            cur.execute(sql, (account_number,))
            wc.commit()
    except Exception as e:
        logger.warning("mark_account_due failed for %s: %s", account_number, e)


def get_display_balance(
    conn,
    account_number: str,
    *,
    refresh: bool = True,
) -> tuple[float, Optional[datetime]]:
    """Drop-in for ``balance_engine.get_balance_kwh`` that prefers the fresh SM value."""
    detail = get_display_balance_detail(conn, account_number, refresh=refresh)
    return detail["balance_kwh"], detail["as_of"]
