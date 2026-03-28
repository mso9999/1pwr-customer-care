#!/usr/bin/env python3
"""
Repair a historical 1Meter hourly_consumption window from preserved raw
meter_readings.

This is intended first for:
  - account: 0026MAK
  - meter:   23022684
  - window:  2026-03-05 06:37 UTC through 2026-03-11 14:38 UTC

The script is dry-run by default. In execute mode it:
  1. backs up the current hourly rows to JSON
  2. deletes only the target `hourly_consumption` iot rows in the window
  3. rebuilds hourly kWh from cumulative `meter_readings.wh_reading`

It deliberately leaves `meter_readings` and `prototype_meter_state` untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

DEFAULT_DSN = os.environ.get("DATABASE_URL", "dbname=onepower_cc")
DEFAULT_ACCOUNT = "0026MAK"
DEFAULT_METER_ID = "23022684"
DEFAULT_START = "2026-03-05T06:37:00+00:00"
DEFAULT_END = "2026-03-11T14:39:00+00:00"  # exclusive; includes the 14:38 sample
DEFAULT_GAP_THRESHOLD_HOURS = 2.0


def parse_ts(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_hour(dt: datetime) -> datetime:
    return ensure_utc(dt).replace(minute=0, second=0, microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return ensure_utc(dt).isoformat()


def load_meter_readings(
    cur,
    meter_id: str,
    account: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    cur.execute(
        """
        SELECT reading_time, wh_reading, power_kw, community
        FROM meter_readings
        WHERE meter_id = %s
          AND source = 'iot'
          AND wh_reading IS NOT NULL
          AND reading_time < %s
        ORDER BY reading_time DESC
        LIMIT 1
        """,
        (meter_id, start),
    )
    prev_row = cur.fetchone()
    prev = None
    if prev_row:
        prev = {
            "reading_time": ensure_utc(prev_row[0]),
            "wh_reading": float(prev_row[1]),
            "power_kw": float(prev_row[2]) if prev_row[2] is not None else None,
            "community": str(prev_row[3] or "").strip() or None,
        }

    cur.execute(
        """
        SELECT reading_time, wh_reading, power_kw, community
        FROM meter_readings
        WHERE meter_id = %s
          AND account_number = %s
          AND source = 'iot'
          AND wh_reading IS NOT NULL
          AND reading_time >= %s
          AND reading_time < %s
        ORDER BY reading_time
        """,
        (meter_id, account, start, end),
    )
    rows = []
    for reading_time, wh_reading, power_kw, community in cur.fetchall():
        rows.append(
            {
                "reading_time": ensure_utc(reading_time),
                "wh_reading": float(wh_reading),
                "power_kw": float(power_kw) if power_kw is not None else None,
                "community": str(community or "").strip() or None,
            }
        )
    return prev, rows


def load_existing_hourly(
    cur,
    meter_id: str,
    account: str,
    start_hour: datetime,
    end_hour: datetime,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT reading_hour, kwh, community
        FROM hourly_consumption
        WHERE meter_id = %s
          AND account_number = %s
          AND source = 'iot'
          AND reading_hour >= %s
          AND reading_hour < %s
        ORDER BY reading_hour
        """,
        (meter_id, account, start_hour, end_hour),
    )
    rows = []
    for reading_hour, kwh, community in cur.fetchall():
        rows.append(
            {
                "reading_hour": ensure_utc(reading_hour),
                "kwh": float(kwh),
                "community": str(community or "").strip() or None,
            }
        )
    return rows


def rebuild_hourly(
    previous_row: dict[str, Any] | None,
    reading_rows: list[dict[str, Any]],
    gap_threshold_hours: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hourly = defaultdict(float)
    gaps = []
    negative_deltas = []
    largest_positive_deltas = []

    prev = previous_row
    positive_delta_sum = 0.0
    for row in reading_rows:
        delta_kwh = None
        gap_hours = None
        if prev is not None:
            gap_hours = (row["reading_time"] - prev["reading_time"]).total_seconds() / 3600.0
            delta_kwh = (row["wh_reading"] - prev["wh_reading"]) / 1000.0
            if gap_hours > gap_threshold_hours:
                gaps.append(
                    {
                        "from": iso(prev["reading_time"]),
                        "to": iso(row["reading_time"]),
                        "gap_hours": round(gap_hours, 2),
                        "delta_kwh_after_gap": round(max(delta_kwh, 0.0), 4),
                    }
                )
            if delta_kwh < 0:
                negative_deltas.append(
                    {
                        "reading_time": iso(row["reading_time"]),
                        "delta_kwh": round(delta_kwh, 4),
                    }
                )
            elif delta_kwh > 0:
                hour_key = floor_hour(row["reading_time"])
                hourly[hour_key] += delta_kwh
                positive_delta_sum += delta_kwh
                largest_positive_deltas.append(
                    {
                        "reading_time": iso(row["reading_time"]),
                        "delta_kwh": round(delta_kwh, 4),
                    }
                )
        prev = row

    largest_positive_deltas.sort(key=lambda item: item["delta_kwh"], reverse=True)
    rebuilt_rows = [
        {"reading_hour": hour_key, "kwh": round(kwh, 4)}
        for hour_key, kwh in sorted(hourly.items())
        if kwh > 0
    ]
    stats = {
        "raw_reading_count": len(reading_rows),
        "rebuilt_hour_count": len(rebuilt_rows),
        "rebuilt_total_kwh": round(positive_delta_sum, 4),
        "gap_count": len(gaps),
        "gaps": gaps,
        "negative_delta_count": len(negative_deltas),
        "negative_deltas": negative_deltas,
        "largest_positive_deltas": largest_positive_deltas[:10],
    }
    return rebuilt_rows, stats


def diff_hourly(
    existing_rows: list[dict[str, Any]],
    rebuilt_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_map = {row["reading_hour"]: round(row["kwh"], 4) for row in existing_rows}
    rebuilt_map = {row["reading_hour"]: round(row["kwh"], 4) for row in rebuilt_rows}
    changed = []
    for hour_key in sorted(set(existing_map) | set(rebuilt_map)):
        current = existing_map.get(hour_key, 0.0)
        rebuilt = rebuilt_map.get(hour_key, 0.0)
        if round(current - rebuilt, 4) != 0:
            changed.append(
                {
                    "reading_hour": iso(hour_key),
                    "current_kwh": current,
                    "rebuilt_kwh": rebuilt,
                    "delta_kwh": round(rebuilt - current, 4),
                }
            )
    return changed


def summarize(existing_rows, rebuilt_rows, changed_rows, stats) -> dict[str, Any]:
    existing_total = round(sum(row["kwh"] for row in existing_rows), 4)
    rebuilt_total = round(sum(row["kwh"] for row in rebuilt_rows), 4)
    return {
        "existing_hour_count": len(existing_rows),
        "existing_total_kwh": existing_total,
        "rebuilt_hour_count": len(rebuilt_rows),
        "rebuilt_total_kwh": rebuilt_total,
        "hour_count_delta": len(rebuilt_rows) - len(existing_rows),
        "total_kwh_delta": round(rebuilt_total - existing_total, 4),
        "changed_hour_count": len(changed_rows),
        "raw_reading_count": stats["raw_reading_count"],
        "gap_count": stats["gap_count"],
        "negative_delta_count": stats["negative_delta_count"],
    }


def write_backup(
    backup_dir: Path,
    meter_id: str,
    account: str,
    start: datetime,
    end: datetime,
    payload: dict[str, Any],
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    path = backup_dir / (
        f"repair_{account}_{meter_id}_{start.strftime('%Y%m%d%H%M')}_"
        f"{end.strftime('%Y%m%d%H%M')}_{stamp}.json"
    )
    path.write_text(json.dumps(payload, indent=2))
    return path


def execute_rebuild(
    conn,
    meter_id: str,
    account: str,
    start_hour: datetime,
    end_hour: datetime,
    rebuilt_rows: list[dict[str, Any]],
    community: str,
) -> tuple[int, int]:
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM hourly_consumption
        WHERE meter_id = %s
          AND account_number = %s
          AND source = 'iot'
          AND reading_hour >= %s
          AND reading_hour < %s
        """,
        (meter_id, account, start_hour, end_hour),
    )
    deleted_rows = cur.rowcount

    batch = [
        (account, meter_id, row["reading_hour"], row["kwh"], community, "iot")
        for row in rebuilt_rows
    ]
    inserted_rows = 0
    if batch:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO hourly_consumption
                (account_number, meter_id, reading_hour, kwh, community, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (meter_id, reading_hour) DO UPDATE
                SET kwh = EXCLUDED.kwh
            """,
            batch,
            page_size=200,
        )
        inserted_rows = len(batch)
    conn.commit()
    return deleted_rows, inserted_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair a historical iot hourly_consumption window from meter_readings."
    )
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="PostgreSQL DSN or DATABASE_URL")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--meter-id", default=DEFAULT_METER_ID)
    parser.add_argument("--start", default=DEFAULT_START, help="Inclusive UTC timestamp")
    parser.add_argument("--end", default=DEFAULT_END, help="Exclusive UTC timestamp")
    parser.add_argument(
        "--gap-threshold-hours",
        type=float,
        default=DEFAULT_GAP_THRESHOLD_HOURS,
        help="Report raw telemetry gaps larger than this threshold",
    )
    parser.add_argument(
        "--backup-dir",
        default="repair_backups",
        help="Directory for JSON backups in execute mode",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the hourly rebuild. Default is preview only.",
    )
    args = parser.parse_args()

    start = parse_ts(args.start)
    end = parse_ts(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    start_hour = floor_hour(start)
    end_hour = floor_hour(end) + timedelta(hours=1)

    conn = psycopg2.connect(args.dsn)
    try:
        previous_row, reading_rows = load_meter_readings(
            conn.cursor(),
            args.meter_id,
            args.account,
            start,
            end,
        )
        if not reading_rows:
            raise SystemExit("No meter_readings found in the requested window")

        existing_rows = load_existing_hourly(
            conn.cursor(),
            args.meter_id,
            args.account,
            start_hour,
            end_hour,
        )
        rebuilt_rows, stats = rebuild_hourly(
            previous_row,
            reading_rows,
            args.gap_threshold_hours,
        )
        changed_rows = diff_hourly(existing_rows, rebuilt_rows)
        summary = summarize(existing_rows, rebuilt_rows, changed_rows, stats)

        preview = {
            "target": {
                "account": args.account,
                "meter_id": args.meter_id,
                "start": iso(start),
                "end_exclusive": iso(end),
                "start_hour": iso(start_hour),
                "end_hour_exclusive": iso(end_hour),
            },
            "summary": summary,
            "previous_baseline": {
                "reading_time": iso(previous_row["reading_time"]) if previous_row else None,
                "wh_reading": previous_row["wh_reading"] if previous_row else None,
            },
            "stats": stats,
            "changed_hours_preview": changed_rows[:25],
        }
        print(json.dumps(preview, indent=2))

        if not args.execute:
            print("\nDry run only. Re-run with --execute to apply this rebuild.")
            return 0

        community = next(
            (row["community"] for row in reading_rows if row.get("community")),
            "MAK",
        )
        backup_payload = {
            "created_at_utc": iso(datetime.now(timezone.utc)),
            "target": preview["target"],
            "summary": summary,
            "stats": stats,
            "existing_hourly_rows": [
                {"reading_hour": iso(row["reading_hour"]), "kwh": row["kwh"], "community": row["community"]}
                for row in existing_rows
            ],
            "rebuilt_hourly_rows": [
                {"reading_hour": iso(row["reading_hour"]), "kwh": row["kwh"]}
                for row in rebuilt_rows
            ],
            "changed_hours": changed_rows,
        }
        backup_path = write_backup(
            Path(args.backup_dir),
            args.meter_id,
            args.account,
            start,
            end,
            backup_payload,
        )
        deleted_rows, inserted_rows = execute_rebuild(
            conn,
            args.meter_id,
            args.account,
            start_hour,
            end_hour,
            rebuilt_rows,
            community,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "backup_path": str(backup_path),
                    "deleted_hourly_rows": deleted_rows,
                    "inserted_hourly_rows": inserted_rows,
                    "summary": summary,
                },
                indent=2,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
