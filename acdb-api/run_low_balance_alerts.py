"""CLI entry point for low-balance SMS alerts — called by systemd timer."""

from __future__ import annotations

import argparse
import logging
import sys

from customer_api import get_connection
from low_balance_alerts import low_balance_tick

logger = logging.getLogger("cc-api.low-balance-alerts")


def main() -> None:
    ap = argparse.ArgumentParser(description="Low-balance customer SMS alerts")
    ap.add_argument("--dry-run", action="store_true", help="Log what would be sent without sending or updating state")
    args = ap.parse_args()

    try:
        with get_connection() as conn:
            stats = low_balance_tick(conn, dry_run=args.dry_run)
    except Exception:
        logger.exception("low_balance_alerts failed")
        sys.exit(1)

    if args.dry_run:
        logger.info(
            "DRY-RUN: %(accounts_seen)d accounts seen, "
            "%(would_send)d would-send, %(skipped_no_phone)d no-phone, "
            "%(skipped_already_warned)d already-warned, %(cleared)d cleared",
            stats,
        )
    else:
        logger.info(
            "Sent %(sent)d low-balance alerts (%(skipped_no_phone)d no-phone, "
            "%(skipped_already_warned)d already-warned, %(cleared)d cleared, "
            "%(accounts_seen)d seen)",
            stats,
        )


if __name__ == "__main__":
    main()
