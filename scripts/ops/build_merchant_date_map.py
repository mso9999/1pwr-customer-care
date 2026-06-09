"""Re-parse Lesotho merchant exports and emit a receipt -> correct date map.

Source of truth for repairing the 2026-05-12 merchant backfill date
transposition (see SESSION_LOG 2026-06-04). The merchant exports are US
``M/D/YYYY``; the original import used a parser that preferred ``%d/%m`` and
transposed every date whose day and month were both <= 12. This script re-parses
the *same* source files with the corrected parser and writes the correct
``paid_at`` per M-Pesa/EcoCash receipt.

Output CSV columns: receipt,paid_at_utc

Usage:
    PYTHONPATH=acdb-api python3 scripts/ops/build_merchant_date_map.py \
        /tmp/merchant_date_map.csv \
        "/Users/mattmso/Dropbox/1PWR/1PWR Financial Records/mobile money records"
"""
from __future__ import annotations

import csv
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, "acdb-api")

from merchant_export_parser import DEFAULT_ROOT, iter_payments_from_root  # noqa: E402


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/merchant_date_map.csv")
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ROOT

    seen: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    parsed = 0

    for payment in iter_payments_from_root(root):
        receipt = (payment.external_id or "").strip()
        if not receipt or not payment.paid_at:
            continue
        iso = payment.paid_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        parsed += 1
        if receipt in seen and seen[receipt] != iso:
            conflicts.append((receipt, seen[receipt], iso))
            continue
        seen[receipt] = iso

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["receipt", "paid_at_utc"])
        for receipt, iso in seen.items():
            w.writerow([receipt, iso])

    print(f"parsed_payments={parsed}")
    print(f"unique_receipts={len(seen)}")
    print(f"receipt_date_conflicts={len(conflicts)}")
    for receipt, a, b in conflicts[:10]:
        print(f"  CONFLICT {receipt}: {a} != {b}")
    print(f"out={out_path}")


if __name__ == "__main__":
    main()
