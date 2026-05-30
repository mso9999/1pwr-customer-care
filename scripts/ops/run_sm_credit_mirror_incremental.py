#!/usr/bin/env python3
"""Run incremental SM->CC historical credit mirror jobs (LS + optional BN)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default="/opt/1pdb/.env")
    ap.add_argument("--state-table", default="sm_credit_mirror_state")
    ap.add_argument("--bootstrap-days", type=int, default=30)
    ap.add_argument("--watermark-overlap-minutes", type=int, default=120)
    ap.add_argument("--fuzzy-window-minutes", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vals = _parse_env_file(args.env_file)
    db_ls = vals.get("DATABASE_URL", "")
    db_bn = vals.get("DATABASE_URL_BN", "")
    if not db_ls:
        raise SystemExit("DATABASE_URL missing from env file")

    here = Path(__file__).resolve()
    importer = here.parent / "import_sm_manual_credits.py"
    if not importer.exists():
        raise SystemExit(f"Importer script not found: {importer}")

    jobs: list[tuple[str, str, str]] = [
        ("LS", "koios", db_ls),
        ("LS", "thundercloud", db_ls),
    ]
    if db_bn:
        jobs.append(("BN", "koios", db_bn))

    run_env = os.environ.copy()
    run_env.update(vals)

    failures = 0
    for country, platform, db_url in jobs:
        cmd = [
            sys.executable,
            str(importer),
            "--database-url",
            db_url,
            "--country",
            country,
            "--platform",
            platform,
            "--days",
            str(max(1, int(args.bootstrap_days))),
            "--use-watermark",
            "--state-table",
            args.state_table,
            "--watermark-overlap-minutes",
            str(max(0, int(args.watermark_overlap_minutes))),
            "--fuzzy-window-minutes",
            str(max(0, int(args.fuzzy_window_minutes))),
        ]
        if not args.dry_run:
            cmd.append("--apply")
        print(f"\n=== {country}/{platform} ===")
        print("CMD:", " ".join(cmd))
        res = subprocess.run(cmd, text=True, capture_output=True, env=run_env)
        if res.stdout.strip():
            print(res.stdout.strip())
        if res.stderr.strip():
            print(res.stderr.strip())
        if res.returncode != 0:
            failures += 1
            print(f"FAILED: {country}/{platform} exit={res.returncode}")

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

