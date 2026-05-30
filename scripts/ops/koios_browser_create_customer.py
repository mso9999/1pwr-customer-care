"""Create a Koios customer via browser automation (Playwright).

The Koios API has a server-side bug: POST /api/v1/customers returns HTTP 201
with a UUID, but the customer is never actually persisted (404 on all subsequent
access). The web UI creates customers correctly, so we automate the browser.

Usage:
  python3 koios_browser_create_customer.py <account_number> <name> <site_code>
      [--meter SERIAL] [--phone PHONE]

Requires: playwright (pip install playwright && playwright install chromium)
Env: KOIOS_WEB_EMAIL[_CC], KOIOS_WEB_PASSWORD[_CC]
"""
import argparse, json, os, re, sys, time


def main():
    parser = argparse.ArgumentParser(description="Create Koios customer via browser automation")
    parser.add_argument("account_number")
    parser.add_argument("name")
    parser.add_argument("site_code", help="Site code (MAS, KET, LSB, GBO, SAM, etc.)")
    parser.add_argument("--meter", help="Meter serial to attach after creation")
    parser.add_argument("--phone", help="Phone number")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--capture-network", action="store_true")
    args = parser.parse_args()

    account_number = args.account_number.strip().upper()
    site_code = args.site_code.strip().upper()

    # Per-country credential resolution
    cc = "BN" if site_code in ("GBO", "SAM") else "LS"
    email = os.environ.get(f"KOIOS_WEB_EMAIL_{cc}") or os.environ.get("KOIOS_WEB_EMAIL", "")
    password = os.environ.get(f"KOIOS_WEB_PASSWORD_{cc}") or os.environ.get("KOIOS_WEB_PASSWORD", "")
    if not email or not password:
        print("ERROR: KOIOS_WEB_EMAIL and KOIOS_WEB_PASSWORD must be set", file=sys.stderr)
        sys.exit(1)

    base_url = os.environ.get("KOIOS_BASE_URL", "https://www.sparkmeter.cloud")

    ORG_IDS = {"LS": "1cddcb07-6647-40aa-aaaa-70d762922029",
               "BN": "0123589c-7f1f-4eb4-8888-d8f8aa706ea4"}
    org_id = ORG_IDS.get(cc, ORG_IDS["LS"])

    # Service area display names
    sa_names = {"KET": "KET", "LSB": "LSB", "MAS": "MAS", "MAT": "MAT",
                "SEH": "SEH", "SHG": "SHG", "TLH": "TLH", "RIB": "RIB",
                "TOS": "TOS", "GBO": "GBO", "SAM": "SAM"}
    sa_name = sa_names.get(site_code, site_code)

    from playwright.sync_api import sync_playwright

    captured = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=args.headless)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        if args.capture_network:
            page.on("request", lambda r: captured.append(
                {"url": r.url, "method": r.method, "post_data": r.post_data}))

        try:
            # 1. Login
            print(f"Logging into {base_url} ({cc})...", file=sys.stderr)
            page.goto(f"{base_url}/login", wait_until="networkidle", timeout=30000)
            page.fill("input[name=email]", email)
            page.fill("input[name=password]", password)
            page.click("button[type=submit]")
            page.wait_for_timeout(8000)
            print(f"   URL: {page.url}", file=sys.stderr)

            # 2. Go to portfolio
            if org_id not in page.url:
                page.goto(f"{base_url}/portfolio/{org_id}/",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(5000)

            # 3. Switch to customer view
            print("   Customer view...", file=sys.stderr)
            for _ in range(3):
                try:
                    page.eval_on_selector_all("span", """els => {
                        for (const el of els) {
                            if (el.textContent.trim()==='Customers' && el.offsetParent)
                            {el.click(); return true;}
                        }
                        return false;
                    }""")
                except Exception:
                    pass
                page.wait_for_timeout(4000)
                if page.query_selector("button[data-test='add-new-customers-button']"):
                    break

            # 4. Open Add New Customer modal
            print("   Opening modal...", file=sys.stderr)
            for _ in range(3):
                try:
                    page.eval_on_selector(
                        "button[data-test='add-new-customers-button']",
                        "el => el.click()")
                except Exception:
                    pass
                page.wait_for_timeout(4000)
                title = page.query_selector(".modal-card-title")
                if title and title.is_visible():
                    break
            else:
                raise Exception("Could not open modal")

            page.wait_for_timeout(2000)

            # 5. Fill Step 1
            print(f"   Filling: {args.name} / {account_number} / {sa_name}",
                  file=sys.stderr)

            # Service area dropdown
            page.eval_on_selector(
                "input[placeholder='Enter or select service area']",
                "el => { el.focus(); el.click(); }")
            page.wait_for_timeout(500)
            page.eval_on_selector(
                "input[placeholder='Enter or select service area']",
                f"el => {{ el.value = '{sa_name}'; "
                f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }}")
            page.wait_for_timeout(2000)
            # Click dropdown option
            page.eval_on_selector_all("*", f"""els => {{
                for (const el of els) {{
                    if (el.textContent.trim()==='{sa_name}' && !el.children.length
                        && el.offsetParent) {{
                        let p=el; while(p&&!p.matches('li,[role=option],.multiselect__option,div[class*=option]')) p=p.parentElement;
                        if(p){{p.click();return;}} el.click(); return;
                    }}
                }}
            }}""")
            page.wait_for_timeout(1000)

            # Name
            page.eval_on_selector(
                "input[placeholder='Enter new customer name']",
                f"el => {{ el.value = '{args.name}'; "
                f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }}")
            # Code
            page.eval_on_selector(
                "input[placeholder='Enter unique customer code']",
                f"el => {{ el.value = '{account_number}'; "
                f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }}")
            # Phone
            if args.phone:
                page.eval_on_selector(
                    "input[placeholder='0000 000 0000']",
                    f"el => {{ el.value = '{args.phone}'; "
                    f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }}")
            page.wait_for_timeout(1000)

            # 6. Next → Step 2
            print("   Step 2 (meter)...", file=sys.stderr)
            page.eval_on_selector(
                "button[data-test='modal-next-button']", "el => el.click()")
            page.wait_for_timeout(4000)

            # 7. Next → Step 3
            print("   Step 3 (confirm)...", file=sys.stderr)
            for _ in range(3):
                try:
                    page.eval_on_selector(
                        "button[data-test='modal-next-button']", "el => el.click()")
                except Exception:
                    pass
                page.wait_for_timeout(3000)
                if page.query_selector("button[data-test='modal-confirm-button']"):
                    break

            # 8. Confirm
            print("   Submitting...", file=sys.stderr)
            page.eval_on_selector(
                "button[data-test='modal-confirm-button']", "el => el.click()")

            # Toast appears briefly (2-3 sec), poll for it immediately
            url = page.url
            cust_id = ""
            success = False

            for _ in range(5):
                page.wait_for_timeout(1500)
                try:
                    toast = page.query_selector(".notification.is-success, [class*=success]")
                    if toast:
                        toast_text = toast.text_content() or ""
                        if "created" in toast_text.lower():
                            success = True
                            print(f"   Toast: {toast_text[:120]}", file=sys.stderr)
                            break
                except Exception:
                    pass

            # Check URL for redirect to customer detail
            if not success and "/customers/" in url:
                m = re.search(
                    r'/customers/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    r'[0-9a-f]{4}-[0-9a-f]{12})', url)
                if m:
                    cust_id = m.group(1)
                    success = True

            # If successful but no ID yet, look up customer via web API
            if success and not cust_id:
                print("   Looking up customer ID...", file=sys.stderr)
                try:
                    # Use Python requests with the session cookie from the browser
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    import requests as _req
                    r = _req.get(
                        f"{base_url}/sm/organizations/{org_id}/"
                        f"unconfigured-customers?page=1&pageSize=50",
                        cookies=cookies, timeout=15)
                    if r.status_code == 200:
                        for c in r.json().get("customers", []):
                            if c.get("code") == account_number:
                                cust_id = c["id"]
                                print(f"   ID={cust_id}", file=sys.stderr)
                                break
                except Exception as e:
                    print(f"   ID lookup error: {e}", file=sys.stderr)

            # Fallback: check page content
            if not success:
                txt = page.content()
                if re.search(r'(?:created|success|saved)', txt, re.IGNORECASE):
                    success = True

            # 10. Attach meter
            if args.meter and cust_id:
                print(f"   Attaching meter {args.meter}...", file=sys.stderr)
                try:
                    page.goto(f"{base_url}/portfolio/{org_id}/customers/{cust_id}",
                              wait_until="networkidle", timeout=15000)
                    page.wait_for_timeout(3000)
                    for s in ["button:has-text('Assign Meter')",
                              "a:has-text('Meter')", "button:has-text('Edit')"]:
                        try:
                            page.click(s, timeout=3000)
                            break
                        except Exception:
                            continue
                    page.fill("input[name='serial']", args.meter)
                    page.click("button[type='submit']")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"   Meter warning: {e}", file=sys.stderr)

            if args.capture_network:
                print("\n=== Network ===", file=sys.stderr)
                for r in captured:
                    if any(x in r["url"] for x in
                           ["customer", "api/v1", "sm/"]):
                        print(f"{r['method']} {r['url']}", file=sys.stderr)
                        if r["post_data"]:
                            print(f"  {r['post_data'][:300]}", file=sys.stderr)

            result = {"success": success, "account_number": account_number,
                      "sm_customer_id": cust_id, "url": url if success else ""}
            print(json.dumps(result))

        except Exception as e:
            try:
                page.screenshot(path="/tmp/koios_create_error.png")
            except Exception:
                pass
            print(f"ERROR: {e}", file=sys.stderr)
            print(json.dumps({"success": False, "error": str(e)}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
