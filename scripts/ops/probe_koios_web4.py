"""Probe Koios - round 4: mimic browser requests exactly with proper headers."""
import json
import re
import requests
import os

BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "mso@1pwrafrica.com")
PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "1PWRBN2026")
ORG_ID = "1cddcb07-6647-40aa-aaaa-70d762922029"
MAS_SA = "e6efc982-91ea-4721-92ee-97e68dd761bb"

s = requests.Session()

# Set browser-like default headers
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
})

# Login
r = s.get(f"{BASE}/login", timeout=30)
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text).group(1)
r = s.post(f"{BASE}/login", data={
    "csrf_token": csrf, "email": EMAIL, "password": PASSWORD,
}, timeout=30)
print(f"Login: HTTP {r.status_code}")

# Get dashboard to capture all cookies
r = s.get(f"{BASE}/dashboard", timeout=30)
print(f"Dashboard: HTTP {r.status_code}")

# Now try the API call with full browser headers
print("\n=== Try with full browser headers + Referer ===")
payload = {"name": "Test Customer", "code": "TEST001", "service_area_id": MAS_SA}
r = s.post(
    f"{BASE}/api/v1/customers",
    json=payload,
    headers={
        "Referer": f"{BASE}/dashboard",
        "Origin": BASE,
        "Accept": "application/json, text/plain, */*",
    },
    timeout=30,
)
print(f"HTTP {r.status_code}: {r.text[:300]}")

# Try with Accept: */* (less restrictive)
print("\n=== Try without Content-Type ===")
r = s.post(
    f"{BASE}/api/v1/customers",
    data=json.dumps(payload),
    headers={
        "Referer": f"{BASE}/dashboard",
        "Origin": BASE,
        "Accept": "*/*",
    },
    timeout=30,
)
print(f"HTTP {r.status_code}: {r.text[:300]}")

# Check if there's a config endpoint that returns the API key
print("\n=== Try config endpoints ===")
for ep in ["/api/v1/config", "/api/v1/me", "/api/v1/session", "/sm/config", "/sm/me", "/sm/session"]:
    r = s.get(f"{BASE}{ep}", timeout=30)
    if r.status_code < 400:
        print(f"GET {ep}: HTTP {r.status_code} - {r.text[:300]}")
    else:
        print(f"GET {ep}: HTTP {r.status_code}")

# Check the organization endpoint
print("\n=== Org info ===")
r = s.get(f"{BASE}/sm/organizations/{ORG_ID}", headers={"Accept": "application/json"}, timeout=30)
if r.status_code < 400:
    print(f"HTTP {r.status_code}: {r.text[:500]}")
else:
    print(f"HTTP {r.status_code}")

# Maybe the CSRF token needs to be refreshed for API calls
print("\n=== Try with fresh CSRF from dashboard ===")
r_dash = s.get(f"{BASE}/dashboard", timeout=30)
# Find CSRF in meta tags or javascript
csrf2_match = re.search(r'csrf_token["\':\s]+["\']([^"\']+)["\']', r_dash.text)
if not csrf2_match:
    csrf2_match = re.search(r'csrf-token["\':\s]+["\']([^"\']+)["\']', r_dash.text)
if not csrf2_match:
    csrf2_match = re.search(r'"[Xx]-[Cc][Ss][Rr][Ff]-[Tt][Oo][Kk][Ee][Nn]"\s*:\s*"([^"]+)"', r_dash.text)
if csrf2_match:
    print(f"Found CSRF: {csrf2_match.group(1)[:50]}")
    r = s.post(
        f"{BASE}/api/v1/customers",
        json=payload,
        headers={"X-CSRF-TOKEN": csrf2_match.group(1)},
        timeout=30,
    )
    print(f"POST with dashboard CSRF: HTTP {r.status_code}: {r.text[:200]}")

# Check what response headers the API gives for a successful request (GET)
print("\n=== Headers for successful GET /api/v1/customers ===")
r = s.get(f"{BASE}/api/v1/customers?code=0193MAS&limit=1", timeout=30)
print(f"HTTP {r.status_code}")
print(f"Response headers: {dict(r.headers)}")
print(f"Body preview: {r.text[:200]}")
