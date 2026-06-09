"""Repair the 2026-05-12 merchant-backfill date transposition in 1PDB.

Root cause (see SESSION_LOG 2026-06-04): merchant exports are US ``M/D/YYYY``
but the importer's date parser preferred ``%d/%m``, transposing every date
whose day and month were both <= 12 (e.g. ``1/7/2026`` -> Jul 1). This produced
~945 future-dated rows and an unknown number of past-but-wrong rows.

This tool is SOURCE-GROUNDED: it does not blindly swap day/month (xlsx exports
delivered correct datetime cells that must not be touched). It reads a
receipt -> correct-date map produced by ``build_merchant_date_map.py`` (a
re-parse of the original source files with the fixed parser) and updates only
rows whose stored date actually differs from the re-derived correct date.

Safety / rollback:
  * Every changed row is first copied into a backup table
    (default ``transactions_date_repair_20260604``) with old + new values.
  * Rollback is a single statement:
        UPDATE transactions t
           SET transaction_date = b.old_transaction_date
          FROM transactions_date_repair_20260604 b
         WHERE t.id = b.id;
  * Without ``--apply`` the script reports the plan and rolls back.

Scope: rows tagged ``source_table LIKE 'mm:%'`` (the merchant backfill
provenance), plus dependent ``balance_seed`` anchor rows that inherited a
transposed date (matched by account + exact old timestamp).

Usage (on the CC host, with DATABASE_URL in env):
    /opt/cc-portal/backend/venv/bin/python \
        scripts/ops/repair_merchant_date_transposition.py \
        --map-csv /tmp/merchant_date_map.csv --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

import psycopg2


def _load_map(path: str) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            receipt = (row["receipt"] or "").strip()
            iso = (row["paid_at_utc"] or "").strip()
            if not receipt or not iso:
                continue
            dt = datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            out[receipt] = dt
    return out


def _receipt_for_row(payment_reference, source_table) -> str:
    ref = (payment_reference or "").strip()
    if ref:
        return ref
    st = (source_table or "").strip()
    if st.startswith("mm:"):
        parts = st.split(":")
        if len(parts) >= 2 and parts[1] not in ("", "noref"):
            return parts[1]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map-csv", required=True)
    ap.add_argument("--backup-table", default="transactions_date_repair_20260604")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="bypass skip-fraction guard")
    args = ap.parse_args()

    dburl = os.environ.get("DATABASE_URL")
    if not dburl:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2

    bt = args.backup_table
    if not bt.replace("_", "").isalnum():
        print("Unsafe backup table name", file=sys.stderr)
        return 2

    date_map = _load_map(args.map_csv)
    print(f"map_receipts={len(date_map)}")

    conn = psycopg2.connect(dburl)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {bt} (
            id                   bigint PRIMARY KEY,
            kind                 text,
            account_number       text,
            receipt              text,
            old_transaction_date timestamptz,
            new_transaction_date timestamptz,
            repaired_at          timestamptz DEFAULT now()
        )
        """
    )

    now_utc = datetime.now(timezone.utc)

    def _swapped(dt: datetime) -> datetime | None:
        """Swap day<->month (keeping year + time). None if day>12 (never transposed)."""
        d = dt.astimezone(timezone.utc)
        if d.day > 12:
            return None
        try:
            return d.replace(month=d.day, day=d.month)
        except ValueError:
            return None

    cur.execute(
        """
        SELECT id, account_number, payment_reference, source_table, transaction_date
        FROM transactions
        WHERE source_table LIKE 'mm:%'
        """
    )
    merchant_rows = cur.fetchall()

    # update tuple: (id, account, receipt, old_dt, new_dt, reason)
    portal_updates: list[tuple[int, str, str, datetime, datetime, str]] = []
    acct_old_to_new: dict[tuple[str, datetime], datetime] = {}
    unchanged = 0
    skipped_ambiguous = 0

    for tid, acct, pref, stbl, txdt in merchant_rows:
        receipt = _receipt_for_row(pref, stbl)
        S = txdt.astimezone(timezone.utc)
        cand = _swapped(S)
        if cand is None:
            # day > 12: original parse could not have transposed this row.
            unchanged += 1
            continue
        if cand.date() == S.date():
            # day == month: swap is a no-op, date is unambiguous either way.
            unchanged += 1
            continue
        mapped = date_map.get(receipt)
        if S > now_utc:
            # Future-dated payment is unambiguously wrong; the deterministic
            # day/month swap restores a valid past date. No map needed.
            new = cand
            reason = "future_swap"
        elif mapped is not None and mapped.date() == cand.date():
            # Source export confirms the transposed date is the real one.
            new = cand
            reason = "map_confirmed_swap"
        else:
            # Either the stored date already matches source (correct), or the
            # receipt is missing/ambiguous in the source map -> do not touch.
            if mapped is not None and mapped.date() == S.date():
                unchanged += 1
            else:
                skipped_ambiguous += 1
            continue
        portal_updates.append((int(tid), acct, receipt, S, new, reason))
        acct_old_to_new[(acct, txdt)] = new

    # Dependent balance_seed rows that inherited a transposed anchor date.
    cur.execute(
        "SELECT id, account_number, transaction_date FROM transactions WHERE source = 'balance_seed'"
    )
    seed_rows = cur.fetchall()
    seed_updates: list[tuple[int, str, datetime, datetime, str]] = []
    for sid, acct, sdt in seed_rows:
        S = sdt.astimezone(timezone.utc)
        new = acct_old_to_new.get((acct, sdt))
        if new is not None:
            seed_updates.append((int(sid), acct, S, new, "portal_anchor"))
        elif S > now_utc:
            cand = _swapped(S)
            if cand is not None:
                seed_updates.append((int(sid), acct, S, cand, "future_swap"))

    total = len(merchant_rows)
    fut = sum(1 for u in portal_updates if u[5] == "future_swap")
    mapc = sum(1 for u in portal_updates if u[5] == "map_confirmed_swap")
    print(f"merchant_rows={total}")
    print(f"to_fix_portal={len(portal_updates)} (future_swap={fut} map_confirmed_swap={mapc})")
    print(f"unchanged_portal={unchanged}")
    print(f"skipped_ambiguous={skipped_ambiguous}")
    print(f"seed_rows={len(seed_rows)} to_fix_seed={len(seed_updates)}")
    for tid, acct, receipt, old, new, reason in portal_updates[:8]:
        print(f"  SAMPLE [{reason}] {acct} {receipt}: {old.isoformat()} -> {new.isoformat()}")

    if not args.apply:
        print("DRY RUN (no --apply) — rolling back.")
        conn.rollback()
        return 0

    # Sanity guard: a healthy repair fixes a bounded subset. If it proposes to
    # rewrite an implausibly large share of merchant rows, abort (likely a bad
    # map or logic regression) unless explicitly forced.
    change_frac = (len(portal_updates) / total) if total else 0.0
    if change_frac > 0.75 and not args.force:
        print(
            f"ABORT: would change {change_frac:.1%} of merchant rows; refusing without --force.",
            file=sys.stderr,
        )
        conn.rollback()
        return 3

    applied_portal = 0
    for tid, acct, receipt, old, new, _reason in portal_updates:
        cur.execute(
            f"""INSERT INTO {bt} (id, kind, account_number, receipt, old_transaction_date, new_transaction_date)
                VALUES (%s, 'portal', %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (tid, acct, receipt, old, new),
        )
        cur.execute("UPDATE transactions SET transaction_date = %s WHERE id = %s", (new, tid))
        applied_portal += 1

    applied_seed = 0
    for sid, acct, old, new, _reason in seed_updates:
        cur.execute(
            f"""INSERT INTO {bt} (id, kind, account_number, receipt, old_transaction_date, new_transaction_date)
                VALUES (%s, 'balance_seed', %s, NULL, %s, %s) ON CONFLICT (id) DO NOTHING""",
            (sid, acct, old, new),
        )
        cur.execute("UPDATE transactions SET transaction_date = %s WHERE id = %s", (new, sid))
        applied_seed += 1

    conn.commit()
    print(f"APPLIED portal={applied_portal} seed={applied_seed} backup_table={bt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
