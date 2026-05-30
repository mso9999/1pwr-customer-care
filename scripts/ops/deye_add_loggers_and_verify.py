#!/usr/bin/env python3
"""Add Deye loggers to account and verify API visibility/live data.

This script is designed to support onboarding execution from:
`docs/ops/deyecloud-logger-onboarding-tracker-2026-05-25.csv`

It can:
1) authenticate against DeyeCloud API (personal -> company -> business token)
2) optionally add missing logger SNs via /v1.0/device/addLogger
3) verify each logger via /v1.0/device/list and /v1.0/device/latest
4) write CSV/JSON reports
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_BASE = os.environ.get("DEYE_API_BASE", "https://eu1-developer.deyecloud.com")
HTTP_TIMEOUT = 30


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(value: str, name: str) -> str:
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _post_json(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:
    resp = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: HTTP {resp.status_code}") from exc
    return data


def _auth_business_token(
    *,
    app_id: str,
    app_secret: str,
    email: str,
    password_plain: str,
    company_id: str,
) -> dict[str, Any]:
    pw_hash = hashlib.sha256(password_plain.encode()).hexdigest()
    body = {
        "appSecret": app_secret,
        "email": email,
        "password": pw_hash,
        "companyId": int(company_id),
    }
    data = _post_json(f"{API_BASE}/v1.0/account/token?appId={app_id}", body=body)
    if not data.get("success"):
        raise RuntimeError(f"Deye business auth failed: {data.get('msg', 'unknown')}")
    return data


def _discover_company_id(*, app_id: str, app_secret: str, email: str, password_plain: str) -> str:
    pw_hash = hashlib.sha256(password_plain.encode()).hexdigest()
    token_data = _post_json(
        f"{API_BASE}/v1.0/account/token?appId={app_id}",
        body={"appSecret": app_secret, "email": email, "password": pw_hash},
    )
    if not token_data.get("success"):
        raise RuntimeError(f"Deye personal auth failed: {token_data.get('msg', 'unknown')}")
    personal_token = token_data.get("accessToken", "")
    if not personal_token:
        raise RuntimeError("Deye personal auth returned no accessToken")

    info = _post_json(
        f"{API_BASE}/v1.0/account/info",
        body={},
        headers={"Authorization": f"Bearer {personal_token}"},
    )
    if not info.get("success"):
        raise RuntimeError(f"Deye account/info failed: {info.get('msg', 'unknown')}")
    orgs = info.get("orgInfoList") or []
    if not orgs:
        raise RuntimeError("No organizations found in Deye account")
    company_id = str(orgs[0].get("companyId") or "")
    if not company_id:
        raise RuntimeError("No companyId found in first orgInfoList entry")
    return company_id


def _api_post(token: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = _post_json(
        f"{API_BASE}{path}",
        body=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if not data.get("success"):
        code = data.get("code")
        msg = data.get("msg", "unknown")
        raise RuntimeError(f"Deye {path} failed: code={code} msg={msg}")
    return data


def _chunks(values: list[str], size: int) -> list[list[str]]:
    out: list[list[str]] = []
    i = 0
    while i < len(values):
        out.append(values[i : i + size])
        i += size
    return out


def _list_devices(token: str, page_size: int = 100) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    total = None
    while page <= 200:
        data = _api_post(token, "/v1.0/device/list", {"page": page, "size": page_size})
        batch = data.get("deviceList") or []
        out.extend(batch)
        if total is None:
            total = int(data.get("total") or 0)
        if not batch or len(batch) < page_size:
            break
        if total and len(out) >= total:
            break
        page += 1
    return out


def _list_stations(token: str, page_size: int = 100) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while page <= 200:
        data = _api_post(token, "/v1.0/station/list", {"page": page, "size": page_size})
        batch = data.get("stationList") or data.get("records") or data.get("dataList") or []
        if not isinstance(batch, list):
            break
        out.extend(batch)
        if not batch or len(batch) < page_size:
            break
        page += 1
    return out


def _latest_nonempty(token: str, sn: str) -> tuple[bool, int, str]:
    data = _api_post(token, "/v1.0/device/latest", {"deviceList": [sn]})
    rows = data.get("deviceDataList") or []
    if not rows:
        return False, 0, "no_deviceDataList_rows"
    first = rows[0] if isinstance(rows[0], dict) else {}
    points = first.get("dataList") or []
    return bool(points), len(points), ""


def _read_tracker(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tracker-csv", required=True)
    ap.add_argument("--output-csv", default="")
    ap.add_argument("--output-json", default="")
    ap.add_argument("--env-file", default="")
    ap.add_argument("--app-id", default=os.environ.get("DEYE_APP_ID", ""))
    ap.add_argument("--app-secret", default=os.environ.get("DEYE_APP_SECRET", ""))
    ap.add_argument("--email", default=os.environ.get("DEYE_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("DEYE_PASSWORD", ""))
    ap.add_argument("--company-id", default=os.environ.get("DEYE_COMPANY_ID", ""))
    ap.add_argument("--add-loggers", action="store_true")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--page-size", type=int, default=100)
    args = ap.parse_args()

    if args.env_file:
        vals = _parse_env_file(args.env_file)
        args.app_id = args.app_id or vals.get("DEYE_APP_ID", "")
        args.app_secret = args.app_secret or vals.get("DEYE_APP_SECRET", "")
        args.email = args.email or vals.get("DEYE_EMAIL", "")
        args.password = args.password or vals.get("DEYE_PASSWORD", "")
        args.company_id = args.company_id or vals.get("DEYE_COMPANY_ID", "")

    app_id = _require(args.app_id, "DEYE_APP_ID/--app-id")
    app_secret = _require(args.app_secret, "DEYE_APP_SECRET/--app-secret")
    email = _require(args.email, "DEYE_EMAIL/--email")
    password = _require(args.password, "DEYE_PASSWORD/--password")

    if not args.company_id:
        args.company_id = _discover_company_id(
            app_id=app_id,
            app_secret=app_secret,
            email=email,
            password_plain=password,
        )

    auth = _auth_business_token(
        app_id=app_id,
        app_secret=app_secret,
        email=email,
        password_plain=password,
        company_id=args.company_id,
    )
    token = str(auth.get("accessToken") or "")
    if not token:
        raise SystemExit("Business auth returned no accessToken")

    tracker_rows = _read_tracker(Path(args.tracker_csv).expanduser().resolve())
    sns = sorted({(r.get("logger_sn") or "").strip() for r in tracker_rows if (r.get("logger_sn") or "").strip()})
    if not sns:
        raise SystemExit("No logger_sn values found in tracker CSV")

    add_results: list[dict[str, Any]] = []
    devices = _list_devices(token, page_size=max(10, int(args.page_size)))
    device_sn_set = {str(d.get("deviceSn") or "").strip() for d in devices if d.get("deviceSn")}

    if args.add_loggers:
        missing = [sn for sn in sns if sn not in device_sn_set]
        for batch in _chunks(missing, max(1, min(10, int(args.batch_size)))):
            data = _api_post(token, "/v1.0/device/addLogger", {"deviceSns": batch})
            add_results.append(
                {
                    "ts_utc": _utc_now(),
                    "deviceSns": batch,
                    "code": data.get("code"),
                    "msg": data.get("msg"),
                    "success": bool(data.get("success")),
                }
            )
        # Refresh device inventory after add attempts.
        devices = _list_devices(token, page_size=max(10, int(args.page_size)))
        device_sn_set = {str(d.get("deviceSn") or "").strip() for d in devices if d.get("deviceSn")}

    # Best-effort station inventory (non-fatal if station/list shape differs).
    station_rows: list[dict[str, Any]] = []
    station_error = ""
    try:
        station_rows = _list_stations(token, page_size=max(10, int(args.page_size)))
    except Exception as exc:  # noqa: BLE001
        station_error = str(exc)

    device_map: dict[str, dict[str, Any]] = {}
    for d in devices:
        sn = str(d.get("deviceSn") or "").strip()
        if sn:
            device_map[sn] = d

    report_rows: list[dict[str, Any]] = []
    for row in tracker_rows:
        sn = (row.get("logger_sn") or "").strip()
        if not sn:
            continue
        in_list = sn in device_sn_set
        latest_ok = False
        latest_points = 0
        latest_err = ""
        if in_list:
            try:
                latest_ok, latest_points, latest_err = _latest_nonempty(token, sn)
            except Exception as exc:  # noqa: BLE001
                latest_err = str(exc)
        d = device_map.get(sn, {})
        out = {
            "site_name": row.get("site_name", ""),
            "country": row.get("country", ""),
            "logger_sn": sn,
            "tracker_station_name": row.get("deye_station_name", ""),
            "tracker_station_id": row.get("deye_station_id", ""),
            "api_device_list_seen": "yes" if in_list else "no",
            "api_latest_nonempty": "yes" if latest_ok else "no",
            "api_latest_points": latest_points,
            "api_latest_error": latest_err,
            "api_device_id": d.get("deviceId", ""),
            "api_device_type": d.get("deviceType", ""),
            "api_device_model": d.get("deviceModel", ""),
            "verified_utc": _utc_now(),
        }
        report_rows.append(out)

    result = {
        "generated_at_utc": _utc_now(),
        "api_base": API_BASE,
        "company_id": str(args.company_id),
        "tracker_csv": str(Path(args.tracker_csv).expanduser().resolve()),
        "add_loggers_mode": bool(args.add_loggers),
        "add_results": add_results,
        "station_list_count": len(station_rows),
        "station_list_error": station_error,
        "device_list_count": len(devices),
        "report_rows": report_rows,
    }

    out_csv = Path(args.output_csv).expanduser().resolve() if args.output_csv else None
    out_json = Path(args.output_json).expanduser().resolve() if args.output_json else None
    if out_csv:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(out_csv, report_rows)
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "company_id": str(args.company_id),
                "device_list_count": len(devices),
                "report_rows": len(report_rows),
                "add_loggers_mode": bool(args.add_loggers),
                "add_batches": len(add_results),
                "output_csv": str(out_csv) if out_csv else "",
                "output_json": str(out_json) if out_json else "",
                "station_list_count": len(station_rows),
                "station_list_error": station_error,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

