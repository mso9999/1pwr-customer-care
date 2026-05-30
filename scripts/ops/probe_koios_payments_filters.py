#!/usr/bin/env python3
"""Probe date-filter params for Koios web payments endpoint."""

from __future__ import annotations

import datetime as dt
import os
import re

import requests

BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
ORG_ID = os.environ.get("KOIOS_ORG_ID", "")
EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "")
PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "")


def login(session: requests.Session) -> None:
    r = session.get(f"{BASE}/login", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("No CSRF token")
    r = session.post(
        f"{BASE}/login",
        data={"csrf_token": m.group(1), "email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Login failed: {r.status_code}")


def main() -> int:
    s = requests.Session()
    login(s)
    date_from = (dt.datetime.utcnow() - dt.timedelta(days=365)).strftime("%Y-%m-%d")
    date_to = dt.datetime.utcnow().strftime("%Y-%m-%d")
    candidates = [
        {"page_size": 100, "page": 1},
        {"page_size": 100, "page": 1, "from": date_from},
        {"page_size": 100, "page": 1, "from": date_from, "to": date_to},
        {"page_size": 100, "page": 1, "start_date": date_from},
        {"page_size": 100, "page": 1, "date_from": date_from},
        {"page_size": 100, "page": 1, "credited_after": date_from},
        {"start": 0, "length": 50},
        {"start": 0, "length": 200},
        {"start": 0, "length": 500},
        {"start": 200, "length": 200},
    ]
    for params in candidates:
        r = s.get(f"{BASE}/sm/organizations/{ORG_ID}/payments", params=params, timeout=90)
        body = r.json() if r.text.strip() else {}
        rows = body.get("payments") or []
        sample = rows[0].get("credited_at") if rows else None
        print({"params": params, "status": r.status_code, "rows": len(rows), "sample_credited_at": sample})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

