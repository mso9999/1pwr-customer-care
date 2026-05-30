"""Probe Koios - round 5: find the actual app entry point and any embedded keys."""
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
csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text).group(1)
s.post(f"{BASE}/login", data={"csrf_token": csrf, "email": EMAIL, "password": PASSWORD}, timeout=30)

# Get dashboard and dump ALL of it
r = s.get(f"{BASE}/dashboard", timeout=30)
html = r.text
print(f"Dashboard: HTTP {r.status_code}, {len(html)} chars")

# Save full HTML for analysis
with open("/tmp/koios_dashboard.html", "w") as f:
    f.write(html)
print("Saved to /tmp/koios_dashboard.html")

# Find ALL script tags (not just prefetch)
print("\n=== All script tags ===")
scripts = re.findall(r'<script[^>]*src="([^"]+)"[^>]*>', html)
for s in scripts[:20]:
    print(f"  {s}")

print("\n=== Inline scripts ===")
inline = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
for i, scr in enumerate(inline[:5]):
    print(f"  Inline #{i}: {len(scr)} chars")
    if len(scr) < 500:
        print(f"    {scr[:300]}")

# Check if there's a webpack manifest or chunk map
print("\n=== Webpack/asset manifest ===")
for pattern in [r'/static/dist/js/runtime[^"]+\.js', r'/static/dist/js/vendor[^"]+\.js',
                r'/static/dist/js/app[^"]+\.js', r'/static/dist/js/main[^"]+\.js']:
    matches = re.findall(pattern, html)
    if matches:
        print(f"  {pattern}: {matches}")
    else:
        print(f"  {pattern}: not found")

# Get ALL JS bundles and search them for API keys
print("\n=== Downloading all JS bundles ===")
all_js = re.findall(r'(?:src|href)="([^"]+)"', html)
js_urls = list(set(u for u in all_js if u.endswith('.js') or '.js?' in u))
print(f"Unique JS URLs: {len(js_urls)}")

# Search each bundle for API client creation (axios, fetch wrapper, etc.)
for js_path in js_urls:
    full_url = js_path if js_path.startswith('http') else f"{BASE}{js_path}"
    try:
        r2 = s.get(full_url, timeout=30)
        if r2.status_code != 200:
            continue
        txt = r2.text
        # Search for API client patterns
        found = []
        for needle in ['baseURL', 'baseUrl', 'base_url', 'createAPI', 'apiClient',
                       'axios.create', 'headers.common', 'interceptors', 'X-API',
                       'apiKey', 'apiSecret', 'API_KEY', 'API_SECRET']:
            if needle in txt:
                found.append(needle)
        if found:
            print(f"\n  {os.path.basename(js_path)} ({len(txt)} chars): {found}")
            # Show context around the first find
            idx = txt.find(found[0])
            print(f"    Context: ...{txt[max(0,idx-50):idx+200]}...")
    except Exception as e:
        pass

# Also try the CSS files - sometimes config is in CSS
print("\n=== Checking CSS for any config ===")
css_urls = [u for u in all_js if u.endswith('.css')]
for css_path in css_urls[:5]:
    full_url = css_path if css_path.startswith('http') else f"{BASE}{css_path}"
    r2 = s.get(full_url, timeout=30)
    if r2.status_code == 200 and 'api' in r2.text.lower():
        print(f"  {css_path}: contains 'api'")
