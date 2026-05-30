#!/usr/bin/env python3
"""Build a manifest for Solarman historical exports and infer cadence quality.

Usage:
  python3 scripts/ops/build_solarman_archive_manifest.py \
    --archive-root archives/solarman/2026-05-25 \
    --output archives/solarman/2026-05-25/manifest.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass
class FileSummary:
    path: str
    bytes: int
    sha256: str
    rows: int | None
    timestamp_column: str | None
    cadence_minutes_mode: int | None
    cadence_quality: str | None
    warnings: list[str]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_csv_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*.csv")):
        if p.is_file():
            yield p


def _parse_ts(raw: str) -> datetime | None:
    txt = (raw or "").strip()
    if not txt:
        return None
    # Common timestamp shapes seen in exports.
    fmts = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(txt, fmt)
        except ValueError:
            continue
    # Try ISO fallback.
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None


def _classify_mode_minutes(mode_minutes: int | None) -> str:
    if mode_minutes is None:
        return "unknown"
    if mode_minutes <= 30:
        return "sub_hourly"
    if 45 <= mode_minutes <= 75:
        return "hourly"
    if mode_minutes >= 120:
        return "daily_or_coarser"
    return "irregular"


def _inspect_csv(path: Path) -> tuple[int, str | None, int | None, str, list[str]]:
    warnings: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h for h in (reader.fieldnames or []) if h]
        if not headers:
            return 0, None, None, "unknown", ["missing_headers"]

        ts_candidates = [
            h for h in headers
            if "time" in h.lower() or "date" in h.lower() or "timestamp" in h.lower()
        ]
        ts_col = ts_candidates[0] if ts_candidates else None

        rows = 0
        parsed: list[datetime] = []
        for row in reader:
            rows += 1
            if ts_col:
                dt = _parse_ts(str(row.get(ts_col, "")))
                if dt is not None:
                    parsed.append(dt)

        if rows == 0:
            warnings.append("empty_csv")
        if ts_col is None:
            warnings.append("timestamp_column_not_found")

        mode_minutes: int | None = None
        quality = "unknown"
        if len(parsed) >= 3:
            parsed.sort()
            deltas = []
            prev = parsed[0]
            for cur in parsed[1:]:
                mins = int((cur - prev).total_seconds() // 60)
                prev = cur
                if mins > 0:
                    deltas.append(mins)
            if deltas:
                mode_minutes = Counter(deltas).most_common(1)[0][0]
                quality = _classify_mode_minutes(mode_minutes)
            else:
                warnings.append("no_positive_time_deltas")
        elif ts_col is not None:
            warnings.append("insufficient_parsed_timestamps")

        return rows, ts_col, mode_minutes, quality, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive-root", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.archive_root).expanduser().resolve()
    out = Path(args.output).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"archive root does not exist: {root}")

    files: list[FileSummary] = []
    for p in _iter_csv_files(root):
        rows, ts_col, mode_minutes, quality, warnings = _inspect_csv(p)
        files.append(
            FileSummary(
                path=str(p.relative_to(root)),
                bytes=p.stat().st_size,
                sha256=_sha256(p),
                rows=rows,
                timestamp_column=ts_col,
                cadence_minutes_mode=mode_minutes,
                cadence_quality=quality,
                warnings=warnings,
            )
        )

    summary_counts = Counter(f.cadence_quality for f in files)
    manifest = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "archive_root": str(root),
        "csv_file_count": len(files),
        "cadence_quality_counts": dict(summary_counts),
        "files": [asdict(f) for f in files],
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(out), "csv_file_count": len(files), "cadence_quality_counts": dict(summary_counts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

