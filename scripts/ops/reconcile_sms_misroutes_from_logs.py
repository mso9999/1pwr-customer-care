#!/usr/bin/env python3
"""
Reconcile historical M-Pesa SMS payments against CC API logs (journalctl export).

Pairs lines like:
  SMS from=... id=... content=...
  SMS payment: txn=... acct=... alloc=... M... from ... ref=... mpesa=...

For each payment, finds the SMS body whose M-Pesa txn id matches ``mpesa=`` on the
payment line (or whose parsed ``txn_id`` matches), re-derives the account that would
have been chosen from the **Remark** field only (first matching ``accounts`` row), and
compares to the credited ``acct`` from the log.

**Limits**
- Default API logs truncate SMS ``content`` to 60 characters (see ``ingest.py``). If the
  Remark/account token falls beyond that window, this script cannot recover the intended
  account — reconciliation is **partial** unless you supply a full-text SMS archive.
- Without ``--database-url``, the script outputs ``intended_from_remark`` as the first
  raw pattern from the Remark line (no DB validation) — use for rough triage only.

Usage:
  journalctl -u 1pdb-api --since "2025-01-01" > /tmp/cc-api.log
  python3 reconcile_sms_misroutes_from_logs.py /tmp/cc-api.log -o /tmp/sms_misroutes.csv
  python3 reconcile_sms_misroutes_from_logs.py a.log b.log --database-url "$DATABASE_URL" -o out.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Repo root: .../scripts/ops -> parents[2]
_REPO = Path(__file__).resolve().parents[2]
_ACDB = _REPO / "acdb-api"
if str(_ACDB) not in sys.path:
    sys.path.insert(0, str(_ACDB))

import psycopg2  # noqa: E402

from mpesa_sms import (  # noqa: E402
    account_exists,
    candidate_accounts_from_text,
    extract_remark_text,
    parse_ls_sms_payment,
)

# Matches ingest.py: logger.info("SMS from=%s id=%s content=%.60s…", ...)
SMS_LINE_RE = re.compile(
    r"SMS from=(?P<frm>\S+)\s+id=(?P<mid>\S+)\s+content=(?P<body>.+)$",
)

PAYMENT_LINE_RE = re.compile(
    r"SMS payment(?:\s+\([^)]+\))?:\s+txn=(?P<txn>\d+)\s+acct=(?P<acct>\S+)\s+alloc=(?P<alloc>\S+)\s+"
    r"M(?P<amt>[\d.]+)\s+from\s+(?P<phone>\S+)\s+ref=(?P<ref>\S+)\s+(?:mpesa|receipt)=(?P<mpesa>\S*)\s*$"
)


def _intended_from_remark_db(conn, content: str, parsed: dict[str, Any]) -> str:
    remark = (parsed.get("remark_raw") or "").strip() or extract_remark_text(content)
    if not remark:
        return ""
    for acct in candidate_accounts_from_text(remark):
        if account_exists(conn, acct):
            return acct
    return ""


def _intended_from_remark_nodb(content: str, parsed: dict[str, Any]) -> str:
    remark = (parsed.get("remark_raw") or "").strip() or extract_remark_text(content)
    if not remark:
        return ""
    cands = candidate_accounts_from_text(remark)
    return cands[0] if cands else ""


def _match_sms_to_payment(
    sms_rows: list[dict[str, Any]],
    mpesa_key: str,
) -> Optional[dict[str, Any]]:
    if not mpesa_key:
        return None
    for row in sms_rows:
        body = row["body"]
        if mpesa_key in body:
            return row
        p = parse_ls_sms_payment(body, row.get("from", ""))
        if p and (p.get("txn_id") or "").strip() == mpesa_key:
            return row
    return None


def _normalize_sms_body(raw: str) -> tuple[str, bool]:
    """Strip trailing ellipsis from logged SMS; flag likely truncation (60-char log cap)."""
    body = raw.rstrip().rstrip("…").rstrip("...")
    truncated = len(body) >= 60
    return body, truncated


def process_log_text(
    text: str,
    conn: Optional[Any],
    all_rows: bool,
) -> list[dict[str, Any]]:
    """Return CSV rows. Default: suspected misroutes and possibly-incomplete truncations."""
    lines = text.splitlines()
    sms_rows: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    for line in lines:
        sm = SMS_LINE_RE.search(line)
        if sm:
            body, trunc = _normalize_sms_body(sm.group("body"))
            sms_rows.append(
                {
                    "from": sm.group("frm"),
                    "id": sm.group("mid"),
                    "body": body,
                    "truncated": trunc,
                }
            )
            continue

        pm = PAYMENT_LINE_RE.search(line)
        if not pm:
            continue

        mpesa = (pm.group("mpesa") or "").strip()
        credited = pm.group("acct").strip()
        txn = pm.group("txn")
        amt = pm.group("amt")
        phone = pm.group("phone").strip()
        alloc = pm.group("alloc").strip()

        sms = _match_sms_to_payment(sms_rows, mpesa)
        if not sms:
            row = {
                "txn_id": txn,
                "credited_account": credited,
                "intended_from_remark": "",
                "payer_phone": phone,
                "amount": amt,
                "mpesa_receipt": mpesa,
                "allocation_logged": alloc,
                "note": "no_matching_sms_body_for_mpesa_key",
            }
            if all_rows:
                out.append(row)
            continue

        body = sms["body"]
        truncated = bool(sms.get("truncated"))

        parsed = parse_ls_sms_payment(body, sms.get("from", "")) or {}
        if conn:
            intended = _intended_from_remark_db(conn, body, parsed)
        else:
            intended = _intended_from_remark_nodb(body, parsed)

        suspected = bool(intended) and intended.upper() != credited.upper()
        note = ""
        if suspected:
            note = "suspected_misroute"
        elif truncated:
            note = "truncated_sms_maybe_incomplete"

        row = {
            "txn_id": txn,
            "credited_account": credited,
            "intended_from_remark": intended,
            "payer_phone": phone,
            "amount": amt,
            "mpesa_receipt": mpesa,
            "allocation_logged": alloc,
            "note": note,
        }

        if all_rows:
            out.append(row)
        elif suspected or truncated:
            out.append(row)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "logs",
        nargs="+",
        help="journalctl export or API log file(s)",
    )
    ap.add_argument("-o", "--output", required=True, help="output CSV path")
    ap.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL URL for 1PDB (validates Remark candidates against accounts)",
    )
    ap.add_argument(
        "--all-rows",
        action="store_true",
        help="emit every payment line, not only suspected misroutes / truncated SMS",
    )
    args = ap.parse_args()

    combined = "\n".join(
        Path(p).read_text(encoding="utf-8", errors="replace") for p in args.logs
    )

    conn = None
    if args.database_url:
        conn = psycopg2.connect(args.database_url)

    try:
        rows = process_log_text(combined, conn, all_rows=args.all_rows)
    finally:
        if conn:
            conn.close()

    fieldnames = [
        "txn_id",
        "credited_account",
        "intended_from_remark",
        "payer_phone",
        "amount",
        "mpesa_receipt",
        "allocation_logged",
        "note",
    ]
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Wrote {len(rows)} row(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
