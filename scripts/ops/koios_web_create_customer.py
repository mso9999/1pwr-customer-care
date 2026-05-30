"""Create a Koios customer via web session auth (bypasses API key permission bug).

The Koios manage API key creates customers that return 201 but are immediately
inaccessible (404 on GET, not in code search). The web UI uses session-based auth
(email/password + CSRF login) which grants full user permissions. This script uses
that same web session to create customers that are fully accessible.

Usage:
  python3 koios_web_create_customer.py <account_number> <name> <site_code> [--meter SERIAL] [--dry-run]

Requires:
  KOIOS_WEB_EMAIL    - SparkMeter web UI login email
  KOIOS_WEB_PASSWORD - SparkMeter web UI password
  KOIOS_BASE_URL     - (optional) defaults to https://www.sparkmeter.cloud

Example:
  KOIOS_WEB_EMAIL=mso@1pwrafrica.com KOIOS_WEB_PASSWORD=... \\
    python3 koios_web_create_customer.py 0273MAS "Mashai Customer 0273" MAS --meter 12345678
"""
import argparse
import os
import re
import sys

import requests

KOIOS_BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
API_TIMEOUT = 90

# Koios service area UUIDs (mirrors sparkmeter_customer.py)
KOIOS_SERVICE_AREAS = {
    "KET": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "LSB": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "MAT": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "SEH": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "SHG": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "TLH": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "RIB": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "TOS": "e3015e87-8dc8-42f0-9cb7-ac93f9473015",
    "MAS": "e6efc982-91ea-4721-92ee-97e68dd761bb",
    "GBO": "de00dfbf-64e7-4d0d-ae80-8a4a309fe8ed",
    "SAM": "43a81ea8-f5fd-4df3-ae6b-0b7f54a58fe2",
}


def koios_web_login(session: requests.Session, site_code: str = "") -> None:
    """Authenticate to Koios web UI via CSRF-form login.

    Resolves credentials per-country: KOIOS_WEB_EMAIL_{CC} → KOIOS_WEB_EMAIL.
    """
    # Determine country code from site (mirrors sparkmeter_customer.py)
    site_to_country = {
        "KET": "LS", "LSB": "LS", "MAS": "LS", "MAT": "LS",
        "SEH": "LS", "SHG": "LS", "TLH": "LS", "RIB": "LS", "TOS": "LS",
        "GBO": "BN", "SAM": "BN",
    }
    cc = site_to_country.get(site_code, "LS")
    email = (
        os.environ.get(f"KOIOS_WEB_EMAIL_{cc}")
        or os.environ.get("KOIOS_WEB_EMAIL", "")
    )
    password = (
        os.environ.get(f"KOIOS_WEB_PASSWORD_{cc}")
        or os.environ.get("KOIOS_WEB_PASSWORD", "")
    )
    if not email or not password:
        raise RuntimeError(
            f"KOIOS_WEB_EMAIL_{cc} (or KOIOS_WEB_EMAIL) and "
            f"KOIOS_WEB_PASSWORD_{cc} (or KOIOS_WEB_PASSWORD) must be set"
        )

    r = session.get(f"{KOIOS_BASE}/login", timeout=30)
    r.raise_for_status()
    csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
    if not csrf:
        raise RuntimeError("Could not find CSRF token on Koios login page")

    r = session.post(
        f"{KOIOS_BASE}/login",
        data={
            "csrf_token": csrf.group(1),
            "email": email,
            "password": password,
        },
        timeout=30,
    )
    if r.status_code != 200 or "/login" in r.url:
        raise RuntimeError(f"Koios web login failed: HTTP {r.status_code}")


def lookup_customer_via_web(session: requests.Session, account_number: str) -> dict | None:
    """Look up a customer by code using web session auth."""
    r = session.get(
        f"{KOIOS_BASE}/api/v1/customers",
        params={"code": account_number},
        timeout=API_TIMEOUT,
    )
    if r.status_code == 200:
        data = r.json().get("data", [])
        return data[0] if data else None
    return None


def create_customer_via_web(
    session: requests.Session,
    account_number: str,
    name: str,
    site_code: str,
    phone: str | None = None,
) -> dict:
    """Create a Koios customer via web session auth. Returns the JSON response."""
    service_area_id = KOIOS_SERVICE_AREAS.get(site_code)
    if not service_area_id:
        raise ValueError(f"No Koios service_area_id for site '{site_code}'")

    payload = {
        "name": name,
        "code": account_number,
        "service_area_id": service_area_id,
    }
    if phone:
        payload["phone_number"] = phone

    r = session.post(
        f"{KOIOS_BASE}/api/v1/customers",
        json=payload,
        timeout=API_TIMEOUT,
    )
    return {"status_code": r.status_code, "body": r.json() if r.content else {}}


def attach_meter_via_web(
    session: requests.Session, customer_id: str, meter_serial: str,
) -> dict:
    """Attach a meter to a Koios customer via web session auth."""
    r = session.put(
        f"{KOIOS_BASE}/api/v1/customers/{customer_id}/meter",
        json={"serial": str(meter_serial).strip()},
        timeout=API_TIMEOUT,
    )
    return {"status_code": r.status_code, "body": r.json() if r.content else {}}


def main():
    parser = argparse.ArgumentParser(description="Create Koios customer via web session")
    parser.add_argument("account_number", help="Customer account code (e.g. 0273MAS)")
    parser.add_argument("name", help="Customer display name")
    parser.add_argument("site_code", help="Site code (e.g. MAS, KET, LSB)")
    parser.add_argument("--meter", help="Meter serial to attach after creation")
    parser.add_argument("--phone", help="Phone number for the customer")
    parser.add_argument("--dry-run", action="store_true", help="Login only, don't create")
    args = parser.parse_args()

    session = requests.Session()
    print(f"Logging into {KOIOS_BASE} (site={args.site_code}) ...")
    koios_web_login(session, args.site_code)
    print("Login OK.\n")

    account_number = args.account_number.strip().upper()

    # Check if already exists
    print(f"Checking if {account_number} already exists ...")
    existing = lookup_customer_via_web(session, account_number)
    if existing:
        sm_id = existing.get("id", "")
        print(f"ALREADY EXISTS: {account_number} -> sm_id={sm_id}")
        if args.meter:
            print(f"Attaching meter {args.meter} ...")
            result = attach_meter_via_web(session, sm_id, args.meter)
            print(f"  Status: {result['status_code']}")
            print(f"  Body: {result['body']}")
        return

    if args.dry_run:
        print(f"DRY RUN: would create {account_number} ({args.name}) at site {args.site_code}")
        return

    # Create
    print(f"Creating {account_number} ({args.name}) at site {args.site_code} ...")
    result = create_customer_via_web(
        session, account_number, args.name, args.site_code, args.phone,
    )
    print(f"  HTTP {result['status_code']}")
    print(f"  Response: {result['body']}")

    if result["status_code"] == 201:
        sm_id = result["body"].get("data", {}).get("id", "")
        print(f"\nSUCCESS: sm_customer_id = {sm_id}")

        # Verify it's accessible
        print(f"\nVerifying customer is accessible ...")
        verified = lookup_customer_via_web(session, account_number)
        if verified:
            print(f"  VERIFIED: {account_number} found via code search (id={verified.get('id')})")
        else:
            print(f"  WARNING: {account_number} NOT FOUND in code search after creation!")

        # Attach meter if requested
        if args.meter and sm_id:
            print(f"\nAttaching meter {args.meter} ...")
            attach_result = attach_meter_via_web(session, sm_id, args.meter)
            print(f"  HTTP {attach_result['status_code']}")
            print(f"  Response: {attach_result['body']}")
    else:
        print(f"\nFAILED: HTTP {result['status_code']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
