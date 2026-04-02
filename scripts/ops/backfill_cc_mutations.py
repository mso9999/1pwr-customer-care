#!/usr/bin/env python3
"""
Backfill legacy SQLite cc_mutations rows into the PostgreSQL audit table.

Intended for one-time cutover runs on the production host after the
`cc_mutations` migration has been applied.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_env_file(path: Path) -> None:
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="/opt/1pdb/.env",
        help="Environment file containing DATABASE_URL and CC_AUTH_DB",
    )
    parser.add_argument(
        "--backend-dir",
        default="/opt/cc-portal/backend",
        help="Path containing customer_api.py and mutations.py",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    backend_dir = Path(args.backend_dir)

    if not env_path.exists():
        raise SystemExit(f"Env file not found: {env_path}")
    if not backend_dir.exists():
        raise SystemExit(f"Backend directory not found: {backend_dir}")

    load_env_file(env_path)
    sys.path.insert(0, str(backend_dir))

    from customer_api import get_connection
    from mutations import _ensure_legacy_backfill

    _ensure_legacy_backfill()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cc_mutations")
        count = int(cursor.fetchone()[0] or 0)
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
