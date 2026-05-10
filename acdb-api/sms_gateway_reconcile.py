#!/usr/bin/env python3
"""Cron job: reconcile SMS gateway LOGIN.TXT against CC/1PDB records.

Fetches the gateway's LOGIN.TXT, extracts M-Pesa receipt keys from
recent payment SMS, and checks each against the local transactions
table.  Any payments that the gateway received but CC never processed
are automatically replayed via the local /api/sms/incoming endpoint.

Designed to run every 15 minutes via systemd timer.

Usage:
    python3 sms_gateway_reconcile.py [--lookback-hours 4] [--dry-run]
"""
import argparse
import json
import logging
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://cc_api@localhost:5432/onepower_cc",
)
GATEWAY_LOGIN_URL = os.environ.get(
    "SMS_GATEWAY_LOGIN_URL",
    "https://sms.1pwrafrica.com/LOGIN.TXT",
)
CC_INGEST_URL = os.environ.get(
    "CC_INGEST_URL",
    "http://127.0.0.1:8100/api/sms/incoming",
)

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(format=LOG_FMT, level=logging.INFO)
logger = logging.getLogger("sms-reconcile")


def fetch_gateway_log() -> str:
    """Download LOGIN.TXT from the SMS gateway."""
    req = urllib.request.Request(GATEWAY_LOGIN_URL)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_recent_payments(log_text: str, lookback_hours: int) -> list[dict]:
    """Extract payment entries from LOGIN.TXT within the lookback window.

    Each line: ``YYYYMMDD_HHMMSS: received sms, payload: {JSON}``
    Returns list of {timestamp, receipt_key, payload_json, amount}.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    results = []

    for line in log_text.splitlines():
        if "payload:" not in line:
            continue

        ts_match = re.match(r"(\d{8}_\d{6}):", line)
        if not ts_match:
            continue

        try:
            ts = datetime.strptime(ts_match.group(1), "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        if ts < cutoff:
            continue

        payload_start = line.find("payload: ") + len("payload: ")
        raw = line[payload_start:].strip()

        # Find balanced JSON
        depth = 0
        end = 0
        for i, ch in enumerate(raw):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0 and i > 0:
                end = i + 1
                break
        if end == 0:
            continue

        payload_json = raw[:end]
        try:
            data = json.loads(payload_json)
        except json.JSONDecodeError:
            continue

        for msg in data.get("messages", []):
            content = msg.get("content", "")
            # Extract M-Pesa receipt (first token, alphanumeric 10-14 chars)
            receipt_match = re.match(r"([A-Z0-9]{8,14})\s+Confirmed", content)
            if not receipt_match:
                continue
            receipt_key = receipt_match.group(1)
            amt_match = re.search(r"M(\d+[\.,]?\d*)", content)
            amount = amt_match.group(1).replace(",", "") if amt_match else "?"

            # Build a single-message payload for replay
            single_payload = json.dumps({
                "messages": [msg],
                "updates": [],
            })
            results.append({
                "timestamp": ts.isoformat(),
                "receipt_key": receipt_key,
                "amount": amount,
                "payload_json": single_payload,
            })

    return results


def check_missing(payments: list[dict]) -> list[dict]:
    """Return payments whose receipt_key is NOT in the transactions table."""
    if not payments:
        return []

    keys = [p["receipt_key"] for p in payments]

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT payment_reference FROM transactions "
            "WHERE payment_reference = ANY(%s)",
            (keys,),
        )
        in_txn = {r[0] for r in cur.fetchall()}

        # Also check sms_inbound_log for recently-received-but-errored
        in_log_errored: set = set()
        try:
            cur.execute(
                "SELECT DISTINCT receipt_key FROM sms_inbound_log "
                "WHERE receipt_key = ANY(%s) AND outcome = 'error'",
                (keys,),
            )
            in_log_errored = {r[0] for r in cur.fetchall()}
        except Exception:
            conn.rollback()
    finally:
        conn.close()

    missing = []
    for p in payments:
        k = p["receipt_key"]
        if k not in in_txn:
            p["reason"] = "errored" if k in in_log_errored else "never_received"
            missing.append(p)
    return missing


def replay_payments(payments: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """POST missing payment payloads to the local CC ingest endpoint."""
    ok = 0
    fail = 0
    for p in payments:
        receipt = p["receipt_key"]
        amount = p["amount"]
        reason = p.get("reason", "unknown")

        if dry_run:
            logger.info("DRY-RUN would replay %s M%s (%s)", receipt, amount, reason)
            ok += 1
            continue

        try:
            req = urllib.request.Request(
                CC_INGEST_URL,
                data=p["payload_json"].encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                logger.info(
                    "Replayed %s M%s (%s) -> HTTP %s",
                    receipt, amount, reason, resp.status,
                )
                ok += 1
        except Exception as e:
            logger.error("Replay failed %s M%s: %s", receipt, amount, e)
            fail += 1

    return ok, fail


def main() -> None:
    parser = argparse.ArgumentParser(description="SMS gateway ↔ CC reconciliation")
    parser.add_argument(
        "--lookback-hours", type=int, default=4,
        help="How many hours back to scan in LOGIN.TXT (default: 4)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report missing payments but do not replay",
    )
    args = parser.parse_args()

    logger.info(
        "SMS reconcile: lookback=%dh gateway=%s",
        args.lookback_hours, GATEWAY_LOGIN_URL,
    )

    try:
        log_text = fetch_gateway_log()
    except Exception as e:
        logger.error("Failed to fetch gateway LOGIN.TXT: %s", e)
        sys.exit(1)

    payments = parse_recent_payments(log_text, args.lookback_hours)
    logger.info("Found %d payment SMS in last %d hours", len(payments), args.lookback_hours)

    if not payments:
        return

    missing = check_missing(payments)
    if not missing:
        logger.info("All %d payments accounted for in CC — no gaps", len(payments))
        return

    logger.warning(
        "%d of %d payments missing from CC — replaying",
        len(missing), len(payments),
    )
    for p in missing:
        logger.warning(
            "  MISSING: %s M%s at %s (%s)",
            p["receipt_key"], p["amount"], p["timestamp"], p.get("reason"),
        )

    ok, fail = replay_payments(missing, dry_run=args.dry_run)
    logger.info("Replay complete: %d OK, %d failed", ok, fail)

    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
