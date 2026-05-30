"""Probe Koios web UI - round 2: cookies, main bundle, and API client config."""
import json
import re
import requests
import os

BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "mso@1pwrafrica.com")
PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "1PWRBN2026")

s = requests.Session()

# Login
r = s.get(f"{BASE}/login", timeout=30)
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text)
r = s.post(f"{BASE}/login", data={"csrf_token": csrf.group(1), "email": EMAIL, "password": PASSWORD}, timeout=30)
print(f"Login: HTTP {r.status_code}")

# Check cookies
print("\n=== Cookies after login ===")
for name, value in s.cookies.items():
    print(f"  {name}: {value[:80] if len(str(value)) > 80 else value}")

# Check login response headers
print("\n=== Login response headers ===")
for k, v in r.headers.items():
    if any(x in k.lower() for x in ['cookie', 'auth', 'token', 'api', 'key', 'session']):
        print(f"  {k}: {v}")

# Check login response body for API keys / tokens
print("\n=== Login response body (first 2000 chars) ===")
print(r.text[:2000])

# Get the main shared chunk (the large one)
print("\n=== Fetching shared chunk ===")
r2 = s.get(f"{BASE}/dashboard", timeout=30)
shared_chunks = re.findall(r'/static/dist/js/[a-z_]+~[a-f0-9]+\.[a-f0-9]+\.js', r2.text)
# Get the largest one (likely the main chunk)
for chunk in shared_chunks[:3]:
    try:
        r3 = s.get(f"{BASE}{chunk}", timeout=30)
        print(f"\n{chunk}: {len(r3.text)} chars")
        # Search for API-related config
        for needle in ['api', 'Api', 'API', 'baseUrl', 'base_url', 'fetch(', 'axios', 'create', 'headers', 'Authorization', 'x-api-key', 'apiKey', 'apiSecret']:
            if needle in r3.text[:50000]:  # only search first 50KB
                idx = r3.text.find(needle)
                count = r3.text[:50000].count(needle)
                print(f"  '{needle}' found {count} times (first at {idx})")
    except Exception as e:
        print(f"  {chunk}: ERROR {e}")

# Try the customer_profile chunk - it may contain customer creation logic
print("\n=== Checking customer_profile chunk ===")
for chunk in shared_chunks:
    if 'customer' in chunk.lower() and 'profile' in chunk.lower():
        r4 = s.get(f"{BASE}{chunk}", timeout=30)
        print(f"{chunk}: {len(r4.text)} chars")
        # Look for create/new/add customer patterns
        for needle in ['create', 'Create', 'new', 'addCustomer', 'createCustomer', 'POST', '/customers', 'service_area']:
            if needle in r4.text:
                count = r4.text.count(needle)
                print(f"  '{needle}' found {count} times")
    elif 'customer' in chunk.lower():
        r4 = s.get(f"{BASE}{chunk}", timeout=30)
        print(f"{chunk}: {len(r4.text)} chars (customer-related)")
