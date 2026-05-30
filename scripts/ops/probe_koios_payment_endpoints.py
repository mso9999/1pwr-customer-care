#!/usr/bin/env python3
"""Probe Koios web-session endpoints for per-payment exports."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests


BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "")
PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "")
ORG_ID = os.environ.get("KOIOS_ORG_ID", "")


def login(session: requests.Session) -> None:
    r = session.get(f"{BASE}/login", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("CSRF token not found")
    r = session.post(
        f"{BASE}/login",
        data={"csrf_token": m.group(1), "email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"login failed: {r.status_code}")


def main() -> int:
    if not EMAIL or not PASSWORD:
        raise SystemExit("KOIOS_WEB_EMAIL and KOIOS_WEB_PASSWORD required")
    s = requests.Session()
    login(s)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidates = [
        f"/transaction/transactions.json?start=0&length=50",
        f"/sm/organizations/{ORG_ID}/transactions?start=0&length=50",
        f"/sm/organizations/{ORG_ID}/payments?start=0&length=50",
        f"/api/v2/report?granularity=monthly&type=payments&date={now}",
        f"/api/v2/report?granularity=daily&type=payments&date={now}",
        f"/api/v2/report?granularity=monthly&type=transactions&date={now}",
        f"/reports/payments.csv?date={now}",
        f"/payments.csv?date={now}",
    ]
    for path in candidates:
        url = f"{BASE}{path}"
        try:
            r = s.get(url, timeout=60)
            print("\nURL:", path)
            print("STATUS:", r.status_code, "CT:", r.headers.get("content-type", ""))
            print((r.text or "")[:500].replace("\n", "\\n"))
        except Exception as exc:
            print("\nURL:", path)
            print("ERROR:", exc)

    print("\n=== Pagination probe: /sm/organizations/{org}/payments ===")
    for page in (1, 2, 3, 10):
        r = s.get(
            f"{BASE}/sm/organizations/{ORG_ID}/payments",
            params={"page_size": 100, "page": page},
            timeout=60,
        )
        j = r.json() if r.text.strip() else {}
        rows = j.get("payments") or []
        first_id = rows[0].get("id") if rows else None
        print(f"page={page} status={r.status_code} rows={len(rows)} first_id={first_id}")

    r = s.get(
        f"{BASE}/sm/organizations/{ORG_ID}/payments",
        params={"start": 0, "length": 100},
        timeout=60,
    )
    j = r.json() if r.text.strip() else {}
    rows = j.get("payments") or []
    first_id = rows[0].get("id") if rows else None
    print(f"start/length status={r.status_code} rows={len(rows)} first_id={first_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

