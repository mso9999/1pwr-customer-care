"""Discover Koios web UI customer creation flow via Playwright."""
import os, sys, json, re
from playwright.sync_api import sync_playwright

BASE = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")
EMAIL = os.environ.get("KOIOS_WEB_EMAIL", "mso@1pwrafrica.com")
PASSWORD = os.environ.get("KOIOS_WEB_PASSWORD", "1PWRBN2026")
LS_ORG_ID = "1cddcb07-6647-40aa-aaaa-70d762922029"
BN_ORG_ID = "0123589c-7f1f-4eb4-8888-d8f8aa706ea4"
OUTPUT_DIR = "/tmp/koios_discovery"

os.makedirs(OUTPUT_DIR, exist_ok=True)

network_log = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    page.on("request", lambda r: network_log.append({
        "method": r.method, "url": r.url,
        "post_data": r.post_data,
        "resource_type": r.resource_type,
    }))

    # Step 1: Login
    print("1. Logging in...", file=sys.stderr)
    page.goto(f"{BASE}/login", wait_until="networkidle", timeout=30000)
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_timeout(5000)  # Let redirect settle
    page.screenshot(path=f"{OUTPUT_DIR}/01_post_login.png", full_page=True)
    print(f"   Current URL after login: {page.url}", file=sys.stderr)

    # Step 2: Check if we're on BN org - if so, switch to LS
    if BN_ORG_ID in page.url:
        print("2. On Benin org, switching to Lesotho...", file=sys.stderr)
        # Try navigating directly to LS portfolio
        try:
            page.goto(f"{BASE}/portfolio/{LS_ORG_ID}/dashboard", wait_until="networkidle", timeout=15000)
        except:
            # Try clicking org switcher
            try:
                page.click("text=Lesotho", timeout=3000)
            except:
                try:
                    page.click("[data-testid=org-switcher]", timeout=3000)
                except:
                    pass
        page.wait_for_timeout(3000)
        print(f"   URL after switch: {page.url}", file=sys.stderr)

    page.screenshot(path=f"{OUTPUT_DIR}/02_ls_dashboard.png", full_page=True)

    # Step 3: Find and click navigation to customers
    print("\n3. Navigation on current page:", file=sys.stderr)
    links = page.eval_on_selector_all("a[href]",
        "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()})).filter(x => x.text.length > 0)")
    for l in links[:30]:
        print(f"   {l['text'][:50]}: {l['href'][:80]}", file=sys.stderr)

    # Find customer-related navigation
    cust_links = [l for l in links if 'customer' in l['text'].lower() or 'customer' in l['href'].lower()]
    print(f"\n   Customer links: {len(cust_links)}", file=sys.stderr)
    for l in cust_links:
        print(f"   -> {l['text'][:50]}: {l['href'][:80]}", file=sys.stderr)

    # Navigate to customers page
    if cust_links:
        page.goto(cust_links[0]['href'], wait_until="networkidle", timeout=15000)
    else:
        # Try common URLs
        for url in [
            f"{BASE}/portfolio/{LS_ORG_ID}/customers",
            f"{BASE}/customers",
            f"{BASE}/organizations/{LS_ORG_ID}/customers",
        ]:
            try:
                page.goto(url, wait_until="networkidle", timeout=10000)
                if page.url != f"{BASE}/login":
                    break
            except:
                pass

    page.wait_for_timeout(2000)
    page.screenshot(path=f"{OUTPUT_DIR}/03_customers.png", full_page=True)
    print(f"\n3. Customers page URL: {page.url}", file=sys.stderr)

    # Step 4: Button analysis
    print("\n4. All buttons on page:", file=sys.stderr)
    buttons = page.eval_on_selector_all("button, a.btn, a[role='button'], a.button",
        "els => els.map(e => ({tag: e.tagName, text: (e.textContent || '').trim(), href: e.href || '', classes: e.className || ''})).filter(x => x.text.length > 0)")
    for b in buttons[:30]:
        print(f"   {b['tag']}: '{b['text'][:60]}' href={b['href'][:60]} classes={b['classes'][:40]}", file=sys.stderr)

    # Step 5: Click "New Customer" or similar
    print("\n5. Trying to find create customer button...", file=sys.stderr)
    clicked = False
    for text in ["New Customer", "Add Customer", "Create Customer", "Add", "New",
                 "+", "Create", "Register Customer", "Add New Customer"]:
        try:
            page.click(f"button:has-text('{text}')", timeout=2000)
            clicked = True
            break
        except:
            try:
                page.click(f"a:has-text('{text}')", timeout=2000)
                clicked = True
                break
            except:
                continue

    if clicked:
        page.wait_for_timeout(2000)
        print(f"   Clicked! New URL: {page.url}", file=sys.stderr)
    else:
        print("   No create button clicked", file=sys.stderr)

    page.screenshot(path=f"{OUTPUT_DIR}/04_after_click.png", full_page=True)

    # Step 6: Look for forms on current page
    print("\n6. Forms on page:", file=sys.stderr)
    forms = page.eval_on_selector_all("form",
        "els => els.map(f => ({action: f.action, method: f.method, inputs: [...f.querySelectorAll('input,select,textarea')].map(i => ({name: i.name, type: i.type, tagName: i.tagName, placeholder: i.placeholder || ''}))}))")
    if forms:
        for f in forms:
            print(f"   form action={f['action']} method={f['method']}", file=sys.stderr)
            for inp in f['inputs'][:15]:
                print(f"     {inp['tagName']} name='{inp['name']}' type={inp['type']} placeholder='{inp['placeholder']}'", file=sys.stderr)
    else:
        print("   No forms found", file=sys.stderr)

    # Step 7: Look for modal dialogs
    modals = page.eval_on_selector_all("[role=dialog], .modal, .v-dialog, [class*=modal], [class*=dialog]",
        "els => els.map(e => ({classes: e.className, visible: e.offsetParent !== null, text: (e.textContent || '').trim().slice(0, 200)}))")
    if modals:
        print("\n7. Modals/dialogs:", file=sys.stderr)
        for m in modals[:5]:
            print(f"   visible={m['visible']} classes={m['classes'][:60]} text={m['text'][:100]}", file=sys.stderr)
    else:
        print("\n7. No modals found", file=sys.stderr)

    # Step 8: Report captured POST requests
    print(f"\n8. Captured POST/PUT requests:", file=sys.stderr)
    for r in network_log:
        if r['method'] in ('POST', 'PUT') and 'static' not in r['url'] and 'google' not in r['url']:
            print(f"\n   {r['method']} {r['url']}", file=sys.stderr)
            if r['post_data']:
                print(f"   Data: {r['post_data'][:500]}", file=sys.stderr)

    # Save full HTML
    html = page.content()
    with open(f"{OUTPUT_DIR}/page_source.html", "w") as f:
        f.write(html)

    browser.close()

print(f"\nDone. Files in {OUTPUT_DIR}/", file=sys.stderr)
