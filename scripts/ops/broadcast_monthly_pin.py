#!/usr/bin/env python3
"""
Broadcast the new monthly staff PIN to every active country's Customer Care
WhatsApp group via the bridge ``/broadcast`` route.

Triggered automatically by ``cc-auth-pin-broadcast.timer`` at ~04:00 UTC on
the 1st of every month, and runnable on demand with the same flags.

Usage::

    # Production: broadcast to every active country
    sudo -u cc_api /opt/cc-portal/backend/venv/bin/python3 \\
        /opt/cc-portal/backend/scripts/ops/broadcast_monthly_pin.py

    # Limit to one or more countries (useful when one bridge is down)
    .../broadcast_monthly_pin.py --country LS
    .../broadcast_monthly_pin.py --country LS --country BN

    # Dry-run: print what would be sent, no HTTP calls
    .../broadcast_monthly_pin.py --dry-run

    # Force a specific year/month (useful for previewing next month)
    .../broadcast_monthly_pin.py --year 2026 --month 6 --dry-run

Exit codes:
  0  every targeted country broadcast succeeded (or dry-run)
  1  at least one broadcast failed
  2  no countries to broadcast to (e.g. all inactive)

The script lives in ``scripts/ops/`` but imports the CC backend modules,
so it must be run with the backend on ``PYTHONPATH``. The systemd unit
shipped beside this file (``cc-auth-pin-broadcast.service``) handles that.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone


def _ensure_backend_on_path() -> None:
    """Add ``/opt/cc-portal/backend`` (or repo ``acdb-api/``) to sys.path so
    ``import auth`` / ``import auth_pin_broadcast`` resolve when the script
    is run standalone from systemd."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("CC_BACKEND_DIR", ""),
        "/opt/cc-portal/backend",
        os.path.normpath(os.path.join(here, "..", "..", "acdb-api")),
    ]
    for path in candidates:
        if path and os.path.isdir(path) and os.path.exists(os.path.join(path, "auth.py")):
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    raise SystemExit(
        "Cannot locate CC backend dir (auth.py). Set CC_BACKEND_DIR env var "
        "or place this script under <repo>/scripts/ops/."
    )


_ensure_backend_on_path()

from auth_pin_broadcast import (  # noqa: E402
    broadcast_pin_for_active_countries,
    compose_pin_message,
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Broadcast the monthly staff PIN to CC WhatsApp groups.",
    )
    parser.add_argument(
        "--country", "-c", action="append", default=None,
        help="Limit to country code(s) (LS, BN, ZM). Repeat for multiple. "
             "Default: every active country.",
    )
    parser.add_argument("--year", type=int, default=None, help="Override year (default: current UTC year).")
    parser.add_argument("--month", type=int, default=None, help="Override month (default: current UTC month).")
    parser.add_argument(
        "--no-next-month", action="store_true",
        help="Don't include next-month advance-notice in the message.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the message that would be sent for each country, but make no HTTP calls.",
    )
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("broadcast_monthly_pin")

    when = datetime.now(timezone.utc)
    if args.year:
        when = when.replace(year=args.year)
    if args.month:
        when = when.replace(month=args.month, day=1)

    if args.dry_run:
        # Render the message without hitting the bridge. We still go via
        # _REGISTRY so the country list matches a real run.
        from country_config import _REGISTRY  # type: ignore[attr-defined]
        targets = [c for c, cfg in _REGISTRY.items() if cfg.active]
        if args.country:
            wanted = {c.upper() for c in args.country}
            targets = [t for t in targets if t in wanted]
        if not targets:
            log.error("No active countries match --country=%s", args.country)
            return 2
        msg = compose_pin_message(
            when.year, when.month, include_next_month=not args.no_next_month,
        )
        for cc in targets:
            print(f"\n=== DRY-RUN: country={cc} year={when.year} month={when.month} ===")
            print(msg)
        return 0

    results = broadcast_pin_for_active_countries(
        when=when,
        include_next_month=not args.no_next_month,
        only=args.country,
    )
    if not results:
        log.error("No active countries to broadcast to (registry has none active or --country filtered all out).")
        return 2

    failed = [r for r in results if not r["ok"]]
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        log.info("[%s] country=%s month=%s pin=%s", status, r["country_code"], r["month_label"], r["pin_prefix"])

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
