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


def _extract_account_from_payload(payload_json: str) -> str | None:
    """Best-effort extract of account reference from the SMS content."""
    try:
        data = json.loads(payload_json)
        content = data["messages"][0].get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError):
        return None
    # Lesotho M-Pesa: "Reference: 0065 MAS"
    m = re.search(r"Reference:\s*(\d{4})\s+([A-Z]{3})", content)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


def _fuzzy_already_credited(cur, account: str | None, amount_str: str,
                            sms_ts_iso: str) -> bool:
    """Check if a payment for the same account+amount exists within ±24h.

    Catches manual ops entries that used a different payment_reference
    (e.g. hand-typed receipt or balance correction).
    """
    if not account:
        return False
    try:
        amt = float(amount_str)
    except (ValueError, TypeError):
        return False
    try:
        ts = datetime.fromisoformat(sms_ts_iso)
    except ValueError:
        return False

    window_start = ts - timedelta(hours=24)
    window_end = ts + timedelta(hours=24)

    cur.execute("""
        SELECT 1 FROM transactions
        WHERE account_number = %s
          AND is_payment = true
          AND transaction_amount BETWEEN %s AND %s
          AND transaction_date BETWEEN %s AND %s
        LIMIT 1
    """, (account, amt - 0.01, amt + 0.01, window_start, window_end))
    if cur.fetchone():
        return True

    # Also check balance_corrections (manual kWh adjustments)
    try:
        cur.execute("""
            SELECT 1 FROM balance_corrections
            WHERE account_number = %s
              AND created_at BETWEEN %s AND %s
            LIMIT 1
        """, (account, window_start, window_end))
        if cur.fetchone():
            return True
    except Exception:
        pass

    return False


def check_missing(payments: list[dict]) -> list[dict]:
    """Return payments not yet credited, with robust duplicate detection.

    Checks three layers:
      1. Exact receipt_key match in transactions.payment_reference
      2. Fuzzy match: same account + same amount ± 24h (catches manual ops)
      3. sms_inbound_log for recently-received-but-errored entries
    """
    if not payments:
        return []

    keys = [p["receipt_key"] for p in payments]

    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()

        # Layer 1: exact receipt match
        cur.execute(
            "SELECT DISTINCT payment_reference FROM transactions "
            "WHERE payment_reference = ANY(%s)",
            (keys,),
        )
        in_txn = {r[0] for r in cur.fetchall()}

        # Layer 3: inbound log errored entries (for reporting)
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

        missing = []
        for p in payments:
            k = p["receipt_key"]

            # Layer 1: exact match
            if k in in_txn:
                continue

            # Layer 2: fuzzy — same account + amount + window
            account = _extract_account_from_payload(p["payload_json"])
            if _fuzzy_already_credited(cur, account, p["amount"], p["timestamp"]):
                logger.info(
                    "Skipping %s M%s — fuzzy match found (account=%s, manual ops likely)",
                    k, p["amount"], account,
                )
                continue

            p["reason"] = "errored" if k in in_log_errored else "never_received"
            p["resolved_account"] = account
            missing.append(p)
    finally:
        conn.close()

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
