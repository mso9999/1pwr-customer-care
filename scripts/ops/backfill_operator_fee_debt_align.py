#!/usr/bin/env python3
"""
Align ``customers.fee_debt_connection_remaining`` / ``fee_debt_readyboard_remaining``
without inserting or updating ``transactions`` (no ``kwh_value`` touches).

This script **only** runs ``UPDATE customers ...`` joined from ``accounts``.

Modes
-----
**--from-db** (review carefully): sets remaining debt to::

    max(0, configured_country_fee - SUM(verified payment_verifications for that type))

- Connection fee uses ``system_config`` keys ``connection_fee_amount`` (fallback:
  ``country_config`` defaults).
- Readyboard uses ``readyboard_fee_amount`` only when
  ``customers.acquires_1pwr_readyboard`` is true; otherwise proposed readyboard
  remaining is **0** (does not force debt for customers who do not acquire RB).

**--fee-debt-csv** ``path``: CSV columns (header row, case-insensitive)::

    account (or account_number), conn_remaining (or fee_debt_connection_remaining),
    rb_remaining (or fee_debt_readyboard_remaining)

Numeric values are applied as-is to the joined ``customers`` row for that account.

Manual SQL (equivalent to --from-db for connection bucket only)::

    -- See script source in repo: proposed values are per-account from
    -- system_config fees minus SUM(payment_verifications.amount) WHERE status='verified'.

Dry-run prints ``account``, current debt columns, and proposed new values.
``DATABASE_URL`` is required.

Examples
--------
  DATABASE_URL=postgresql://... PYTHONPATH=acdb-api python3 \\
      scripts/ops/backfill_operator_fee_debt_align.py --from-db --dry-run

  DATABASE_URL=postgresql://... PYTHONPATH=acdb-api python3 \\
      scripts/ops/backfill_operator_fee_debt_align.py \\
      --fee-debt-csv /tmp/manual_fee_debt.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ACDB_API = ROOT / "acdb-api"
if str(ACDB_API) not in sys.path:
    sys.path.insert(0, str(ACDB_API))

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(format=LOG_FMT, level=logging.INFO)
logger = logging.getLogger("cc-ops.fee-debt-align")


def _load_country_fees(conn) -> dict[str, float]:
    from country_config import COUNTRY

    fees = {
        "connection_fee_amount": float(COUNTRY.default_connection_fee),
        "readyboard_fee_amount": float(COUNTRY.default_readyboard_fee),
    }
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT key, value FROM system_config
            WHERE key IN ('connection_fee_amount', 'readyboard_fee_amount')
            """
        )
        for key, value in cur.fetchall():
            if value is not None and str(value).strip():
                fees[key] = float(value)
    except Exception as exc:
        logger.warning("system_config fee read failed: %s", exc)
    return fees


def _iter_from_db_proposals(conn) -> list[dict[str, Any]]:
    fees = _load_country_fees(conn)
    cf = float(fees["connection_fee_amount"])
    rb = float(fees["readyboard_fee_amount"])
    cur = conn.cursor()
    cur.execute(
        """
        WITH pv AS (
            SELECT account_number,
                COALESCE(SUM(amount) FILTER (
                    WHERE payment_type = 'connection_fee' AND status = 'verified'), 0
                ) AS conn_paid,
                COALESCE(SUM(amount) FILTER (
                    WHERE payment_type = 'readyboard_fee' AND status = 'verified'), 0
                ) AS rb_paid
            FROM payment_verifications
            GROUP BY account_number
        )
        SELECT
            a.account_number,
            c.id AS customer_id,
            COALESCE(c.fee_debt_connection_remaining, 0) AS cur_conn,
            COALESCE(c.fee_debt_readyboard_remaining, 0) AS cur_rb,
            GREATEST(0, %s - COALESCE(pv.conn_paid, 0)) AS prop_conn,
            GREATEST(
                0,
                CASE WHEN COALESCE(c.acquires_1pwr_readyboard, false)
                     THEN %s - COALESCE(pv.rb_paid, 0)
                     ELSE 0 END
            ) AS prop_rb
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        LEFT JOIN pv ON pv.account_number = a.account_number
        ORDER BY a.account_number
        """,
        (cf, rb),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _normalize_csv_header(name: str) -> str:
    return "".join((name or "").lower().split()).replace("_", "")


def _load_csv_updates(path: Path) -> list[dict[str, Any]]:
    """Rows: account, optional conn_remaining, optional rb_remaining (omit = leave unchanged)."""
    out: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("CSV has no header row")
        for row in reader:
            acc: str | None = None
            cr: float | None = None
            rr: float | None = None
            for key, val in row.items():
                nk = _normalize_csv_header(key or "")
                if nk in ("account", "accountnumber"):
                    acc = (val or "").strip().upper()
                elif nk in ("connremaining", "feedebtconnectionremaining"):
                    cr = float(val) if val not in (None, "") else 0.0
                elif nk in ("rbremaining", "feedebtreadyboardremaining"):
                    rr = float(val) if val not in (None, "") else 0.0
            if not acc:
                continue
            if cr is None and rr is None:
                continue
            rec: dict[str, Any] = {"account": acc}
            if cr is not None:
                rec["conn_remaining"] = cr
            if rr is not None:
                rec["rb_remaining"] = rr
            out.append(rec)
    return out


def _apply_customer_debt(
    conn,
    customer_id: int,
    conn_remaining: float,
    rb_remaining: float,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE customers
           SET fee_debt_connection_remaining = %s,
               fee_debt_readyboard_remaining = %s
         WHERE id = %s
        """,
        (conn_remaining, rb_remaining, customer_id),
    )


def _resolve_customer_id(conn, account_number: str) -> int | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id
        FROM accounts a
        JOIN customers c ON c.id = a.customer_id
        WHERE a.account_number = %s
        LIMIT 1
        """,
        (account_number,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--from-db",
        action="store_true",
        help="Propose updates from country fees minus verified fee payments",
    )
    mode.add_argument("--fee-debt-csv", type=Path, help="CSV with explicit remaining debt")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_false", dest="apply", help="Print only (default)")
    g.add_argument("--apply", action="store_true", dest="apply", help="Persist UPDATEs")
    ap.set_defaults(apply=False)
    args = ap.parse_args()
    apply = bool(args.apply)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        logger.error("DATABASE_URL is required")
        return 2

    import psycopg2

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    try:
        proposals: list[dict[str, Any]] = []
        if args.from_db:
            proposals = _iter_from_db_proposals(conn)
        else:
            if not args.fee_debt_csv or not args.fee_debt_csv.is_file():
                logger.error("--fee-debt-csv must point to an existing file")
                return 1
            csv_rows = _load_csv_updates(args.fee_debt_csv)
            for r in csv_rows:
                cid = _resolve_customer_id(conn, str(r["account"]))
                if cid is None:
                    proposals.append(
                        {
                            "account_number": r["account"],
                            "customer_id": None,
                            "cur_conn": None,
                            "cur_rb": None,
                            "prop_conn": r.get("conn_remaining", 0.0),
                            "prop_rb": r.get("rb_remaining", 0.0),
                            "note": "unknown_account",
                        }
                    )
                    continue
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT fee_debt_connection_remaining, fee_debt_readyboard_remaining
                    FROM customers WHERE id = %s
                    """,
                    (cid,),
                )
                row = cur.fetchone()
                cur_c = float(row[0] or 0)
                cur_r = float(row[1] or 0)
                new_c = float(r["conn_remaining"]) if "conn_remaining" in r else cur_c
                new_r = float(r["rb_remaining"]) if "rb_remaining" in r else cur_r
                proposals.append(
                    {
                        "account_number": r["account"],
                        "customer_id": cid,
                        "cur_conn": cur_c,
                        "cur_rb": cur_r,
                        "prop_conn": new_c,
                        "prop_rb": new_r,
                    }
                )

        n_change = 0
        for p in proposals:
            acc = p.get("account_number")
            cur_c = p.get("cur_conn")
            cur_r = p.get("cur_rb")
            new_c = float(p["prop_conn"])
            new_r = float(p["prop_rb"])
            if cur_c is not None and abs(cur_c - new_c) < 0.005 and abs((cur_r or 0) - new_r) < 0.005:
                continue
            n_change += 1
            logger.info(
                "%s customer_id=%s conn %.2f -> %.2f | rb %.2f -> %.2f %s",
                acc,
                p.get("customer_id"),
                float(cur_c or 0) if cur_c is not None else -1,
                new_c,
                float(cur_r or 0) if cur_r is not None else -1,
                new_r,
                p.get("note") or "",
            )
            if apply and p.get("customer_id") is not None:
                _apply_customer_debt(conn, int(p["customer_id"]), new_c, new_r)

        logger.info("Rows with debt change: %d (of %d scanned)", n_change, len(proposals))
        if apply:
            conn.commit()
            logger.info("Committed fee debt alignment")
        else:
            conn.rollback()
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
