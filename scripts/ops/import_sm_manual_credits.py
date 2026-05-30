#!/usr/bin/env python3
"""Backfill SparkMeter-side credits that are missing from CC.

Goal:
- Keep CC as the single operator surface while preserving historical SparkMeter credits.
- Import only credits not already represented in CC (idempotent by reference/fingerprint).

Notes:
- This script does NOT push anything to SparkMeter; it only inserts CC transactions.
- It is safe to run repeatedly (dry-run default, apply requires --apply).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg2
import requests

HERE = Path(__file__).resolve()
SEARCH_ROOTS = []
if len(HERE.parents) >= 3:
    SEARCH_ROOTS.append(HERE.parents[2])
SEARCH_ROOTS.extend(
    [
        Path.cwd(),
        Path("/opt/cc-portal/backend"),
        Path("/opt/cc-portal"),
    ]
)

for root in SEARCH_ROOTS:
    if root.exists() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    acdb = root / "acdb-api"
    ops = root / "scripts" / "ops"
    for p in (acdb, ops):
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))

from balance_engine import record_payment_kwh  # noqa: E402
from country_config import get_tariff_rate_for_site  # noqa: E402

LOG = logging.getLogger("sm_manual_import")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
TC_BASE = os.environ.get("TC_API_BASE", "https://sparkcloud-u740425.sparkmeter.cloud")

LS_KOIOS_SERVICE_AREAS = {
    "KET": "e1ef0c38-298d-4fef-bc7d-78a645fe325d",
    "LSB": "328ceae8-8b57-4173-b54b-82481d833d6a",
    "MAS": "e6efc982-91ea-4721-92ee-97e68dd761bb",
    "MAT": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "SEH": "402e4b83-45bb-4dea-a276-ac99927514cb",
    "SHG": "f54a1658-1763-4ba7-8bf3-fbf71bed97fe",
    "TLH": "f8b5d05e-3a29-4e65-a0ad-6e60c0f2d85b",
    "RIB": "8b574fc5-8f59-4bd8-b1d4-2882a0747abb",
    "TOS": "6cbc921c-62e2-49d2-8b20-0b0ab38b2005",
}
BN_KOIOS_SERVICE_AREAS = {
    "GBO": "de00dfbf-a0f6-4577-89c4-f01e6a84553d",
    "SAM": "43a81ea8-a7ec-4af0-82d0-2132f6323677",
}


@dataclass
class CreditRow:
    platform: str  # koios | thundercloud
    account_number: str
    meter_id: str
    amount: float
    kwh: float | None
    occurred_at: datetime
    external_id: str
    fingerprint: str


@dataclass
class ExistingIndex:
    refs: set[str]
    txn_ids: set[int]


@dataclass
class MirrorState:
    last_credited_at: datetime | None
    last_external_id: str
    last_status: str
    last_message: str
    last_candidates: int
    last_inserted: int


def _parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    txt = raw.strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _site_code(account_number: str) -> str:
    m = re.search(r"([A-Z]{2,4})$", (account_number or "").upper())
    return m.group(1) if m else ""


def _normalize_account(value: str | None) -> str:
    return (value or "").strip().upper()


def _amount_from(obj: dict[str, Any], keys: Iterable[str]) -> float:
    for key in keys:
        if key not in obj:
            continue
        try:
            amt = float(obj.get(key) or 0)
            return amt
        except (TypeError, ValueError):
            continue
    return 0.0


def _str_from(obj: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        val = obj.get(key)
        if val is None:
            continue
        txt = str(val).strip()
        if txt:
            return txt
    return ""


def _koios_web_login(session: requests.Session, country: str) -> None:
    cc = country.upper()
    email = os.environ.get(f"KOIOS_WEB_EMAIL_{cc}") or os.environ.get("KOIOS_WEB_EMAIL", "")
    password = os.environ.get(f"KOIOS_WEB_PASSWORD_{cc}") or os.environ.get("KOIOS_WEB_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            f"KOIOS_WEB_EMAIL_{cc} (or KOIOS_WEB_EMAIL) and "
            f"KOIOS_WEB_PASSWORD_{cc} (or KOIOS_WEB_PASSWORD) are required"
        )
    r = session.get(f"{KOIOS_BASE}/login", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not find Koios CSRF token")
    r = session.post(
        f"{KOIOS_BASE}/login",
        data={"csrf_token": m.group(1), "email": email, "password": password},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Koios web login failed: HTTP {r.status_code}")


def fetch_koios_credits(country: str, date_from: datetime, date_to: datetime) -> list[CreditRow]:
    session = requests.Session()
    _koios_web_login(session, country)
    org_id = os.environ.get("KOIOS_ORG_ID", "").strip()
    if not org_id:
        raise RuntimeError("KOIOS_ORG_ID is required for Koios web payments import")

    # Build lookup maps from customer roster.
    customer_id_to_code: dict[str, str] = {}
    meter_serial_to_code: dict[str, str] = {}
    cust_page = 1
    while True:
        r = session.get(
            f"{KOIOS_BASE}/sm/organizations/{org_id}/customers",
            headers={"Accept": "application/json"},
            params={"page_size": 500, "page": cust_page},
            timeout=90,
        )
        r.raise_for_status()
        body = r.json() if r.text.strip() else {}
        customers = body.get("customers") or []
        if not customers:
            break
        for c in customers:
            code = _normalize_account(_str_from(c, ("code", "customer_code")))
            if not code:
                continue
            cid = _str_from(c, ("id", "customer_id"))
            if cid:
                customer_id_to_code[cid] = code
            for key in ("meter_serial", "meter_id"):
                serial = _str_from(c, (key,))
                if serial:
                    meter_serial_to_code[serial] = code
            for meter in (c.get("meters") or []):
                serial = _str_from(meter, ("serial_number", "serial", "meter_serial", "meter_id"))
                if serial:
                    meter_serial_to_code[serial] = code
        if len(customers) < 500:
            break
        cust_page += 1

    out: list[CreditRow] = []
    page = 1
    while True:
        r = session.get(
            f"{KOIOS_BASE}/sm/organizations/{org_id}/payments",
            params={"page_size": 100, "page": page},
            timeout=90,
        )
        r.raise_for_status()
        body = r.json() if r.text.strip() else {}
        batch = body.get("payments") or []
        if not batch:
            break
        stop = False
        for item in batch:
            occurred = _as_dt(_str_from(item, ("credited_at", "created_at", "created", "timestamp", "date")))
            if not occurred:
                continue
            if occurred < date_from:
                stop = True
                continue
            if occurred > date_to:
                continue
            amount_obj = item.get("amount") or {}
            amount = _amount_from(amount_obj, ("value",))
            if amount <= 0:
                amount = _amount_from(item, ("amount", "total_amount", "payment_amount", "cost"))
            if amount <= 0:
                continue
            status = _str_from(item, ("status",)).lower()
            if status in {"reversed", "failed", "cancelled"}:
                continue

            recipient_id = _str_from(item, ("recipient_id",))
            meter_id = _str_from(item, ("customer_meter_serial", "meter_serial", "meter_id"))
            account = _normalize_account(_str_from(item, ("customer_code", "code", "recipient_code")))
            if not account and recipient_id:
                account = _normalize_account(customer_id_to_code.get(recipient_id))
            if not account and meter_id:
                account = _normalize_account(meter_serial_to_code.get(meter_id))
            if not account:
                continue
            ext = _str_from(item, ("external_id", "id", "payment_id"))
            kwh = _amount_from(item, ("energy", "kilowatt_hours", "kwh", "total_energy"))
            kwh_value = kwh if kwh > 0 else None
            fp = f"smhist:koios:{ext or account + ':' + occurred.isoformat() + ':' + str(amount)}"
            out.append(
                CreditRow(
                    platform="koios",
                    account_number=account,
                    meter_id=meter_id,
                    amount=round(amount, 4),
                    kwh=kwh_value,
                    occurred_at=occurred,
                    external_id=ext,
                    fingerprint=fp[:180],
                )
            )
        LOG.info("Koios web payments page %d processed (running total %d)", page, len(out))
        if stop:
            break
        page += 1
        time.sleep(0.2)
    return out


def _tc_login(session: requests.Session) -> None:
    email = os.environ.get("THUNDERCLOUD_USERNAME", "")
    password = os.environ.get("THUNDERCLOUD_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("THUNDERCLOUD_USERNAME and THUNDERCLOUD_PASSWORD are required")
    r = session.get(f"{TC_BASE}/login", timeout=45, verify=False)
    r.raise_for_status()
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not find ThunderCloud CSRF token")
    r = session.post(
        f"{TC_BASE}/login",
        data={"csrf_token": m.group(1), "email": email, "password": password},
        timeout=45,
        verify=False,
        allow_redirects=True,
    )
    if "/login" in r.url:
        raise RuntimeError("ThunderCloud login failed")


def fetch_thundercloud_credits(date_from: datetime, date_to: datetime) -> list[CreditRow]:
    import warnings

    warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")
    session = requests.Session()
    _tc_login(session)

    meter_map: dict[str, str] = {}
    r_m = session.get(f"{TC_BASE}/meter/meters.json?meter_type=customer", timeout=45, verify=False)
    if r_m.status_code == 200:
        for rec in (r_m.json().get("meters") or []):
            serial = _str_from(rec, ("meter_serial",))
            code = _normalize_account(_str_from(rec, ("customer_code",)))
            if serial and code:
                meter_map[serial] = code

    out: list[CreditRow] = []
    start = 0
    page_size = 200
    while start < 20000:
        r = session.get(
            f"{TC_BASE}/transaction/transactions.json?start={start}&length={page_size}",
            timeout=60,
            verify=False,
        )
        if r.status_code != 200:
            break
        body = r.json()
        txns = body.get("transactions") or []
        if not txns:
            break
        stop = False
        for item in txns:
            if _str_from(item, ("acct_type",)).lower() != "credit":
                continue
            occurred = _as_dt(_str_from(item, ("created", "created_at", "timestamp")))
            if not occurred:
                continue
            if occurred < date_from:
                stop = True
                continue
            if occurred > date_to:
                continue
            amount = _amount_from(item, ("amount", "total_amount"))
            if amount <= 0:
                continue
            to_data = item.get("to_data") or {}
            meter_id = _str_from(to_data, ("meter_serial", "meter_id"))
            account = _normalize_account(_str_from(to_data, ("customer_code",)))
            if not account and meter_id:
                account = _normalize_account(meter_map.get(meter_id))
            if not account:
                continue
            ext = _str_from(item, ("external_id", "id"))
            fp = f"smhist:thundercloud:{ext or account + ':' + occurred.isoformat() + ':' + str(amount)}"
            out.append(
                CreditRow(
                    platform="thundercloud",
                    account_number=account,
                    meter_id=meter_id,
                    amount=round(amount, 4),
                    kwh=None,
                    occurred_at=occurred,
                    external_id=ext,
                    fingerprint=fp[:180],
                )
            )
        if stop:
            break
        start += page_size
        time.sleep(0.2)
    LOG.info("ThunderCloud fetched: %d", len(out))
    return out


def _cc_has_reference(conn, ref: str) -> bool:
    if not ref:
        return False
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM transactions
        WHERE (
            lower(trim(COALESCE(payment_reference, ''))) = lower(trim(%s))
            OR lower(trim(COALESCE(source_table, ''))) = lower(trim(%s))
        )
        LIMIT 1
        """,
        (ref, ref),
    )
    return cur.fetchone() is not None


def _looks_like_cc_txn_id(ext: str) -> bool:
    return bool(ext and ext.isdigit())


def _cc_has_txn_id(conn, txn_id: str) -> bool:
    if not txn_id or not txn_id.isdigit():
        return False
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM transactions WHERE id = %s LIMIT 1", (int(txn_id),))
    return cur.fetchone() is not None


def _fuzzy_already_recorded(conn, account: str, amount: float, when: datetime, window_minutes: int) -> bool:
    if window_minutes <= 0:
        return False
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM transactions
        WHERE account_number = %s
          AND is_payment = true
          AND ABS(COALESCE(transaction_amount, 0) - %s) <= 0.01
          AND ABS(EXTRACT(EPOCH FROM (transaction_date - %s))) < %s
        LIMIT 1
        """,
        (account, amount, when, int(window_minutes) * 60),
    )
    return cur.fetchone() is not None


def _rate_for_account(conn, account: str) -> float:
    site = _site_code(account)
    if site:
        rate = float(get_tariff_rate_for_site(site) or 0)
        if rate > 0:
            return rate
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = 'tariff_rate' LIMIT 1")
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _payment_ref(row: CreditRow) -> str:
    token = row.external_id or row.fingerprint
    return f"sm_manual_hist:{row.platform}:{token}"[:120]


def _source_table(row: CreditRow) -> str:
    # transactions.source_table is varchar(50) in production.
    return f"smhist:{row.platform}:{row.fingerprint}"[:50]


def _norm_ref(value: str | None) -> str:
    return (value or "").strip().lower()


def _safe_table_name(value: str) -> str:
    txt = (value or "").strip()
    if not re.match(r"^[a-z_][a-z0-9_]*$", txt):
        raise ValueError(f"Unsafe table name: {value!r}")
    return txt


def _build_existing_index(conn, rows: list[CreditRow]) -> ExistingIndex:
    ref_candidates: set[str] = set()
    txn_id_candidates: set[int] = set()
    for row in rows:
        ext = (row.external_id or "").strip()
        if ext:
            ref_candidates.add(ext)
            if ext.isdigit():
                txn_id_candidates.add(int(ext))
        ref_candidates.add(_payment_ref(row))
        ref_candidates.add(_source_table(row))

    refs: set[str] = set()
    if ref_candidates:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT lower(trim(COALESCE(payment_reference, ''))) AS pr,
                   lower(trim(COALESCE(source_table, ''))) AS st
            FROM transactions
            WHERE (
                payment_reference = ANY(%s)
                OR source_table = ANY(%s)
            )
            """,
            (list(ref_candidates), list(ref_candidates)),
        )
        for pr, st in cur.fetchall():
            if pr:
                refs.add(str(pr))
            if st:
                refs.add(str(st))

    txn_ids: set[int] = set()
    if txn_id_candidates:
        cur = conn.cursor()
        cur.execute("SELECT id FROM transactions WHERE id = ANY(%s)", (list(txn_id_candidates),))
        txn_ids = {int(r[0]) for r in cur.fetchall()}

    return ExistingIndex(refs=refs, txn_ids=txn_ids)


def _has_transactions_source_table(conn) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'transactions'
          AND column_name = 'source_table'
        LIMIT 1
        """
    )
    return cur.fetchone() is not None


def _ensure_state_table(conn, table_name: str) -> None:
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            country_code TEXT NOT NULL,
            platform TEXT NOT NULL CHECK (platform IN ('koios', 'thundercloud')),
            last_credited_at TIMESTAMPTZ,
            last_external_id TEXT NOT NULL DEFAULT '',
            last_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_status TEXT NOT NULL DEFAULT 'unknown',
            last_message TEXT NOT NULL DEFAULT '',
            last_candidates INTEGER NOT NULL DEFAULT 0,
            last_inserted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (country_code, platform)
        )
        """
    )


def _load_state(conn, table_name: str, country: str, platform: str) -> MirrorState:
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT last_credited_at, COALESCE(last_external_id, ''),
               COALESCE(last_status, ''), COALESCE(last_message, ''),
               COALESCE(last_candidates, 0), COALESCE(last_inserted, 0)
        FROM {table_name}
        WHERE country_code = %s AND platform = %s
        """,
        (country, platform),
    )
    row = cur.fetchone()
    if not row:
        return MirrorState(
            last_credited_at=None,
            last_external_id="",
            last_status="",
            last_message="",
            last_candidates=0,
            last_inserted=0,
        )
    return MirrorState(
        last_credited_at=row[0],
        last_external_id=str(row[1] or ""),
        last_status=str(row[2] or ""),
        last_message=str(row[3] or ""),
        last_candidates=int(row[4] or 0),
        last_inserted=int(row[5] or 0),
    )


def _save_state(
    conn,
    table_name: str,
    *,
    country: str,
    platform: str,
    last_credited_at: datetime | None,
    last_external_id: str,
    last_status: str,
    last_message: str,
    last_candidates: int,
    last_inserted: int,
) -> None:
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {table_name}
            (country_code, platform, last_credited_at, last_external_id,
             last_run_at, last_status, last_message, last_candidates, last_inserted)
        VALUES
            (%s, %s, %s, %s, NOW(), %s, %s, %s, %s)
        ON CONFLICT (country_code, platform) DO UPDATE
        SET last_credited_at = EXCLUDED.last_credited_at,
            last_external_id = EXCLUDED.last_external_id,
            last_run_at = NOW(),
            last_status = EXCLUDED.last_status,
            last_message = EXCLUDED.last_message,
            last_candidates = EXCLUDED.last_candidates,
            last_inserted = EXCLUDED.last_inserted
        """,
        (
            country,
            platform,
            last_credited_at,
            (last_external_id or "")[:200],
            (last_status or "")[:50],
            (last_message or "")[:800],
            int(last_candidates),
            int(last_inserted),
        ),
    )


def _insert_missing_credit(
    conn,
    row: CreditRow,
    dry_run: bool,
    fuzzy_window_minutes: int,
    existing: ExistingIndex,
    has_source_table: bool,
) -> str:
    # Skip if external id clearly points to existing CC transaction/payload.
    ext_norm = _norm_ref(row.external_id)
    if row.external_id:
        if _looks_like_cc_txn_id(row.external_id) and int(row.external_id) in existing.txn_ids:
            return "skip_cc_external_id"
        if ext_norm in existing.refs:
            return "skip_cc_reference"

    pref = _payment_ref(row)
    src_tbl = _source_table(row)
    pref_norm = _norm_ref(pref)
    src_norm = _norm_ref(src_tbl)
    if pref_norm in existing.refs or src_norm in existing.refs:
        return "skip_already_imported"

    if _fuzzy_already_recorded(
        conn, row.account_number, row.amount, row.occurred_at, fuzzy_window_minutes
    ):
        return "skip_fuzzy_duplicate"

    rate = _rate_for_account(conn, row.account_number)
    if rate <= 0:
        return "skip_no_rate"

    if dry_run:
        existing.refs.add(pref_norm)
        existing.refs.add(src_norm)
        return "would_insert"

    source_value = "thundercloud" if row.platform == "thundercloud" else "koios"
    txn_id, _, _ = record_payment_kwh(
        conn=conn,
        account_number=row.account_number,
        meter_id=row.meter_id or "",
        amount_currency=row.amount,
        rate=rate,
        kwh_override=row.kwh,
        source=source_value,
        timestamp=row.occurred_at,
        payment_reference=pref,
        ledger_amount_currency=row.amount,
    )
    if has_source_table:
        cur = conn.cursor()
        cur.execute("UPDATE transactions SET source_table = %s WHERE id = %s", (src_tbl, int(txn_id)))
    LOG.info(
        "Imported SM historical credit txn=%s platform=%s acct=%s amount=%.2f ext=%s",
        txn_id,
        row.platform,
        row.account_number,
        row.amount,
        row.external_id or "-",
    )
    existing.refs.add(pref_norm)
    existing.refs.add(src_norm)
    return "inserted"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--env-file", default="", help="Optional env file to read DATABASE_URL from")
    ap.add_argument("--country", choices=["LS", "BN"], default=os.environ.get("COUNTRY_CODE", "LS").upper())
    ap.add_argument("--from-ts", default="", help="Inclusive UTC ISO timestamp lower bound")
    ap.add_argument("--to-ts", default="", help="Inclusive UTC ISO timestamp upper bound")
    ap.add_argument("--days", type=int, default=365, help="Look-back window in days")
    ap.add_argument("--platform", choices=["koios", "thundercloud", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="Max candidate rows to process (0 = all)")
    ap.add_argument("--use-watermark", action="store_true", help="Use persisted watermark lower bound")
    ap.add_argument(
        "--watermark-overlap-minutes",
        type=int,
        default=120,
        help="Backtrack from watermark by this many minutes for safety overlap",
    )
    ap.add_argument("--state-table", default="sm_credit_mirror_state")
    ap.add_argument(
        "--fuzzy-window-minutes",
        type=int,
        default=0,
        help="Optional fuzzy duplicate window (0 disables fuzzy duplicate checks for faster runs).",
    )
    ap.add_argument("--apply", action="store_true", help="Write to DB (default dry-run)")
    args = ap.parse_args()

    if not args.database_url and args.env_file:
        vals = _parse_env_file(args.env_file)
        args.database_url = vals.get("DATABASE_URL", "")
        for k, v in vals.items():
            os.environ.setdefault(k, v)
    if not args.database_url:
        raise SystemExit("DATABASE_URL (or --env-file with DATABASE_URL) is required")
    try:
        args.state_table = _safe_table_name(args.state_table)
    except ValueError as exc:
        raise SystemExit(str(exc))

    until = _parse_ts(args.to_ts) or datetime.now(timezone.utc)
    explicit_since = _parse_ts(args.from_ts)

    conn = psycopg2.connect(args.database_url)
    state = MirrorState(None, "", "", "", 0, 0)
    if args.use_watermark:
        _ensure_state_table(conn, args.state_table)
        if args.platform == "all":
            raise SystemExit("--use-watermark requires a single --platform value")
        state = _load_state(conn, args.state_table, args.country, args.platform)

    if explicit_since is not None:
        since = explicit_since
    elif args.use_watermark and state.last_credited_at is not None:
        since = state.last_credited_at - timedelta(minutes=max(0, int(args.watermark_overlap_minutes)))
    else:
        since = until - timedelta(days=max(1, int(args.days)))

    rows: list[CreditRow] = []
    if args.platform in {"koios", "all"}:
        rows.extend(fetch_koios_credits(args.country, since, until))
    if args.platform in {"thundercloud", "all"} and args.country == "LS":
        rows.extend(fetch_thundercloud_credits(since, until))

    rows.sort(key=lambda r: r.occurred_at)
    if args.limit > 0:
        rows = rows[: args.limit]

    counters: dict[str, int] = {}
    try:
        existing = _build_existing_index(conn, rows)
        has_source_table = _has_transactions_source_table(conn)
        for row in rows:
            status = _insert_missing_credit(
                conn,
                row,
                dry_run=not args.apply,
                fuzzy_window_minutes=max(0, int(args.fuzzy_window_minutes)),
                existing=existing,
                has_source_table=has_source_table,
            )
            counters[status] = counters.get(status, 0) + 1
        inserted = int(counters.get("inserted", 0))
        last_seen = max((r.occurred_at for r in rows), default=until)
        last_ext = ""
        if rows:
            for r in reversed(rows):
                if r.external_id:
                    last_ext = r.external_id
                    break
        if args.use_watermark and args.platform != "all":
            _save_state(
                conn,
                args.state_table,
                country=args.country,
                platform=args.platform,
                last_credited_at=last_seen,
                last_external_id=last_ext or state.last_external_id,
                last_status="ok_apply" if args.apply else "ok_dry_run",
                last_message=f"window={since.isoformat()}..{until.isoformat()}",
                last_candidates=len(rows),
                last_inserted=inserted,
            )
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "country": args.country,
            "platform": args.platform,
            "window_from": since.isoformat(),
            "window_to": until.isoformat(),
            "candidates": len(rows),
            "watermark_mode": bool(args.use_watermark),
            "results": counters,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

