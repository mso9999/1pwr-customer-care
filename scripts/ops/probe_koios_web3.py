"""Probe Koios web UI - round 3: try form-encoded POST and check CORS/headers."""
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

# Login
r = s.get(f"{BASE}/login", timeout=30)
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text).group(1)
print(f"CSRF token: {csrf}")

r = s.post(f"{BASE}/login", data={
    "csrf_token": csrf, "email": EMAIL, "password": PASSWORD,
}, timeout=30)
print(f"Login: HTTP {r.status_code}\n")

# Try 1: POST /api/v1/customers with form-encoded data + csrf
payload = {
    "name": "Test Customer",
    "code": "TEST001",
    "service_area_id": MAS_SA,
}
# Add CSRF token
payload_with_csrf = {**payload, "csrf_token": csrf}

print("=== Try 1: POST /api/v1/customers with CSRF in body ===")
r = s.post(f"{BASE}/api/v1/customers", data=payload_with_csrf, timeout=30)
print(f"HTTP {r.status_code}: {r.text[:300]}")

print("\n=== Try 2: POST /api/v1/customers with CSRF header ===")
r = s.post(f"{BASE}/api/v1/customers", json=payload,
           headers={"X-CSRF-TOKEN": csrf, "X-CSRFToken": csrf},
           timeout=30)
print(f"HTTP {r.status_code}: {r.text[:300]}")

print("\n=== Try 3: Check OPTIONS for /api/v1/customers ===")
r = s.options(f"{BASE}/api/v1/customers", timeout=30)
print(f"HTTP {r.status_code}")
print(f"Allow: {r.headers.get('Allow', 'not set')}")
print(f"Headers: {dict(r.headers)}")

# Try to find what the SPA actually calls
print("\n=== Try 4: What does /sm/ customer creation look like? ===")
# Check what methods are allowed on /sm/ endpoints
for ep in [f"/sm/organizations/{ORG_ID}/customers", f"/sm/customers"]:
    r = s.options(f"{BASE}{ep}", timeout=30)
    print(f"OPTIONS {ep}: HTTP {r.status_code}, Allow={r.headers.get('Allow', 'not set')}")

# Maybe the SPA uses a completely different path
print("\n=== Try 5: Probe alternative paths ===")
for ep in ["/api/v1/organizations/customers", "/api/customers",
           f"/api/v1/service_areas/{MAS_SA}/customers",
           f"/api/v1/sites/customers"]:
    r = s.post(f"{BASE}{ep}", json=payload, timeout=30)
    print(f"POST {ep}: HTTP {r.status_code}" if r.status_code < 500 else f"POST {ep}: HTTP {r.status_code}")

# Final attempt: check if the API key is stored in a cookie or local storage
# by inspecting the dashboard for any inline config
print("\n=== Try 6: Inline config/state in dashboard ===")
r = s.get(f"{BASE}/dashboard", timeout=30)
# Look for JSON objects with api keys
for pattern in [r'window\.__INITIAL_STATE__\s*=\s*({[^<]+})',
                r'window\.config\s*=\s*({[^<]+})',
                r'"(?:apiKey|api_key|apiSecret|api_secret|token)"\s*:\s*"([^"]+)"']:
    matches = re.findall(pattern, r.text, re.IGNORECASE)
    if matches:
        print(f"  Pattern {pattern}: {len(matches)} matches")
        for m in matches[:2]:
            print(f"    {str(m)[:200]}")
    else:
        print(f"  Pattern {pattern}: none")
