"""Probe Koios web UI to find the embedded API key and customer creation endpoint."""
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
if not csrf:
    print("FATAL: CSRF token not found")
    exit(1)
r = s.post(f"{BASE}/login", data={"csrf_token": csrf.group(1), "email": EMAIL, "password": PASSWORD}, timeout=30)
print(f"Login: HTTP {r.status_code}, logged_in={('/login' not in r.url)}")

# Get dashboard
r = s.get(f"{BASE}/dashboard", timeout=30)
print(f"Dashboard: HTTP {r.status_code}, {len(r.text)} chars")

# Find ALL JS URLs
all_js = re.findall(r'(?:src|href)="([^"]+)"', r.text)
js_urls = [u for u in all_js if u.endswith('.js') or '.js?' in u]
print(f"\nJS URLs found: {len(js_urls)}")
for u in js_urls[:10]:
    print(f"  {u}")

# Try to find API keys in the dashboard HTML itself
print("\n=== Searching dashboard HTML ===")
for needle in ['X-API-KEY', 'apiKey', 'api_key', 'sogk1Ne', 'API_KEY', 'x-api-key', 'apiKey:', 'apiSecret']:
    if needle in r.text:
        idx = r.text.find(needle)
        print(f"Found {needle!r}: ...{r.text[max(0,idx-20):idx+200]}...")

# Fetch each JS bundle and search
print("\n=== Searching JS bundles ===")
for js_path in js_urls[:15]:
    full_url = js_path if js_path.startswith('http') else f"{BASE}{js_path}"
    try:
        r2 = s.get(full_url, timeout=30)
        if r2.status_code != 200:
            continue
        txt = r2.text
        found_any = False
        for needle in ['X-API-KEY', 'apiKey', 'api_key', 'sogk1Ne', 'API_KEY', 'x-api-key', 'apiKey:', 'apiSecret', 'X-API-SECRET']:
            if needle in txt:
                if not found_any:
                    print(f"\n{js_path} ({len(txt)} chars):")
                    found_any = True
                # Find all occurrences
                idx = 0
                count = 0
                while needle in txt[idx:] and count < 5:
                    pos = txt.index(needle, idx)
                    snippet = txt[max(0,pos-30):pos+250]
                    print(f"  {needle}: ...{snippet}...")
                    idx = pos + 1
                    count += 1
        if not found_any:
            pass  # print(f"  {js_path}: nothing relevant")
    except Exception as e:
        print(f"  {js_path}: ERROR {e}")

# Also try: the SPA might use a different API host
print("\n=== Searching for API URLs ===")
try:
    r3 = s.get(f"{BASE}/dashboard", timeout=30)
    api_urls = re.findall(r"https?://[^\"' ]*api[^\"' ]*", r3.text)
    print(f"API URLs found: {api_urls[:5]}")
except Exception as e:
    print(f"Error: {e}")
