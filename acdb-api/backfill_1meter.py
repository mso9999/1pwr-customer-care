#!/usr/bin/env python3
"""
Backfill prototype 1Meter data from S3 JSON into 1PDB via the API.

Downloads the S3 JSON backup, filters to known meters, downsamples to
~5-minute intervals, and POSTs each reading to /api/meters/reading.

Usage:
    python3 backfill_1meter.py                    # dry run
    python3 backfill_1meter.py --execute          # actually POST
    python3 backfill_1meter.py --execute --all    # include unmapped meters (skipped)
"""

import argparse
import json
import re
import sys
import time
import urllib.request

S3_URL = "https://1meterdatacopy.s3.amazonaws.com/1meter_data_s3_copy.json"
API_URL = "https://cc.1pwrafrica.com/api/meters/reading"
IOT_KEY = "1pwr-iot-ingest-2026"

KNOWN_METERS = {
    "000023022673", "23022673",
    "000023022628", "23022628",
    "000023022696", "23022696",
}

MIN_INTERVAL_MINUTES = 5


def strip_units(val, default=0.0):
    """Extract leading number from '230.1 V', '3.01 kWh', etc."""
    s = str(val).strip()
    try:
        return float(s)
    except ValueError:
        m = re.match(r"[-+]?\d*\.?\d+", s)
        return float(m.group()) if m else default


def download_s3_data():
    print(f"Downloading {S3_URL} ...")
    with urllib.request.urlopen(S3_URL, timeout=60) as resp:
        data = json.loads(resp.read())
    print(f"  {len(data)} total records")
    return data


def downsample(records, interval_minutes=MIN_INTERVAL_MINUTES):
    """Keep one record per interval_minutes window per meter."""
    records.sort(key=lambda r: r.get("Time", ""))
    last_accepted = {}
    kept = []

    for r in records:
        mid = r.get("meterId", "")
        ts = r.get("Time", "")
        if not ts:
            continue
        try:
            minutes = int(ts[:4]) * 525960 + int(ts[4:6]) * 43800 + int(ts[6:8]) * 1440 + int(ts[8:10]) * 60 + int(ts[10:12])
        except (ValueError, IndexError):
            continue

        prev = last_accepted.get(mid, 0)
        if minutes - prev >= interval_minutes:
            kept.append(r)
            last_accepted[mid] = minutes

    return kept


def post_reading(record, dry_run=False):
    mid = record["meterId"]
    payload = {
        "meter_id": mid,
        "timestamp": record["Time"],
        "energy_active": strip_units(record.get("EnergyActive", 0)),
        "power_active": strip_units(record.get("PowerActive", record.get("Power", 0))),
        "voltage": strip_units(record.get("Voltage", 0)),
        "current": strip_units(record.get("Current", 0)),
        "relay": str(record.get("Relay", "0")),
        "frequency": strip_units(record.get("Frequency", 0)),
    }

    if dry_run:
        return {"status": "dry_run", "meter_id": mid}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-IoT-Key": IOT_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {"error": e.code, "detail": error_body}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Backfill 1Meter data from S3 to 1PDB")
    parser.add_argument("--execute", action="store_true", help="Actually POST (default is dry run)")
    parser.add_argument("--all", action="store_true", help="Include unmapped meters")
    parser.add_argument("--interval", type=int, default=MIN_INTERVAL_MINUTES,
                        help=f"Min minutes between readings (default {MIN_INTERVAL_MINUTES})")
    args = parser.parse_args()

    data = download_s3_data()

    if not args.all:
        data = [r for r in data if r.get("meterId", "") in KNOWN_METERS]
        print(f"  Filtered to known meters: {len(data)} records")

    sampled = downsample(data, args.interval)
    print(f"  Downsampled to {args.interval}-min intervals: {len(sampled)} records")

    from collections import Counter
    meter_counts = Counter(r["meterId"] for r in sampled)
    for mid, cnt in sorted(meter_counts.items()):
        ea_vals = [strip_units(r.get("EnergyActive", 0)) for r in sampled if r["meterId"] == mid]
        print(f"    {mid}: {cnt} readings, energy {min(ea_vals):.2f} → {max(ea_vals):.2f} kWh")

    if not args.execute:
        print("\nDry run — pass --execute to actually POST")
        return

    print(f"\nPosting {len(sampled)} readings to {API_URL} ...")
    ok = 0
    errors = 0
    for i, record in enumerate(sampled):
        result = post_reading(record, dry_run=False)
        if result.get("status") == "ok":
            ok += 1
        else:
            errors += 1
            if errors <= 5:
                print(f"  Error #{errors}: {record['meterId']} {record['Time']} → {result}")

        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(sampled)} sent ({ok} ok, {errors} errors)")
            time.sleep(0.1)

    print(f"\nDone: {ok} ok, {errors} errors out of {len(sampled)} readings")


if __name__ == "__main__":
    main()
