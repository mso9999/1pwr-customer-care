"""Patch sparkmeter_customer.py for Koios API resilience.

Changes:
  1. Add import time
  2. Add _koios_paginated_lookup() fallback for code-search failures
  3. lookup_sparkmeter_customer: try pagination when code search returns empty
  4. _koios_create_customer: retry 502/504; handle already-exists gracefully
  5. attach_koios_meter: retry 502/504 on PUT
"""

PATH = '/opt/cc-portal/backend/sparkmeter_customer.py'
with open(PATH) as f:
    content = f.read()

# 1. Add import time
content = content.replace('import re\n', 'import re\nimport time\n', 1)

# 2. Add _koios_paginated_lookup before lookup_sparkmeter_customer
paginated_lookup_func = '''

def _koios_paginated_lookup(account_number: str, site: str, max_pages: int = 30) -> Optional[dict]:
    """Fallback: paginate through all customers to find by code.

    Koios code search sometimes returns empty even when the customer exists.
    """
    cursor = None
    headers = _koios_headers(site)
    for _ in range(max_pages):
        params = {}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=headers,
                params=params,
                timeout=API_TIMEOUT,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        data = r.json()
        customers = data.get("data") or []
        for c in customers:
            if c.get("code") == account_number:
                return c
        cursor = data.get("cursor")
        if not cursor:
            break
    return None
'''

old_def = '\ndef lookup_sparkmeter_customer(account_number: str) -> Optional[dict]:'
content = content.replace(old_def, paginated_lookup_func + old_def, 1)

# 3. Update lookup_sparkmeter_customer — add pagination fallback
old_lookup = """    try:
        if site in THUNDERCLOUD_SITES:
            r = requests.get(
                f"{TC_API_BASE}/api/v0/customer/{account_number}",
                headers={"Authentication-Token": TC_AUTH_TOKEN},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                customers = r.json().get("customers", [])
                return customers[0] if customers else None
            return None
        elif site in KOIOS_SERVICE_AREAS:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=_koios_headers(site),
                params={"code": account_number},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                return data[0] if data else None
            return None
    except Exception as e:
        logger.warning("SM customer lookup failed for %s: %s", account_number, e)
    return None"""

new_lookup = """    try:
        if site in THUNDERCLOUD_SITES:
            r = requests.get(
                f"{TC_API_BASE}/api/v0/customer/{account_number}",
                headers={"Authentication-Token": TC_AUTH_TOKEN},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                customers = r.json().get("customers", [])
                return customers[0] if customers else None
            return None
        elif site in KOIOS_SERVICE_AREAS:
            r = requests.get(
                f"{KOIOS_BASE}/api/v1/customers",
                headers=_koios_headers(site),
                params={"code": account_number},
                timeout=API_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    return data[0]
            # Fallback: paginate through all customers
            logger.info(
                "SM code search empty for %s, trying pagination fallback",
                account_number,
            )
            fallback = _koios_paginated_lookup(account_number, site)
            if fallback:
                logger.info(
                    "SM customer found via pagination for %s: %s",
                    account_number, fallback.get("id"),
                )
            return fallback
    except Exception as e:
        logger.warning("SM customer lookup failed for %s: %s", account_number, e)
    return None"""

content = content.replace(old_lookup, new_lookup, 1)

# 4. Update _koios_create_customer — retries + already-exists handling
old_create = """    try:
        r = requests.post(
            f"{KOIOS_BASE}/api/v1/customers",
            json=payload,
            headers=_koios_headers(site_code),
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        logger.error("Koios customer create failed for %s: %s", account_number, e)
        return CustomerSyncResult(success=False, platform="koios", error=str(e))

    if r.status_code == 201:
        data = r.json().get("data", {})
        sm_id = data.get("id", "")
        logger.info(
            "Koios customer created: %s -> %s (sm_id=%s)",
            account_number, name, sm_id,
        )
        return CustomerSyncResult(
            success=True, platform="koios", sm_customer_id=str(sm_id),
        )

    errors = r.json().get("errors", [])
    error_msg = errors[0].get("title", str(errors)) if errors else f"HTTP {r.status_code}"
    logger.warning(
        "Koios customer create failed for %s: %s", account_number, error_msg,
    )
    return CustomerSyncResult(success=False, platform="koios", error=error_msg)"""

new_create = """    retries = 3
    last_error = None
    last_status = None
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{KOIOS_BASE}/api/v1/customers",
                json=payload,
                headers=_koios_headers(site_code),
                timeout=API_TIMEOUT,
            )
        except Exception as e:
            logger.error(
                "Koios customer create failed for %s (attempt %d/%d): %s",
                account_number, attempt + 1, retries, e,
            )
            last_error = str(e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        last_status = r.status_code
        if r.status_code == 201:
            data = r.json().get("data", {})
            sm_id = data.get("id", "")
            logger.info(
                "Koios customer created: %s -> %s (sm_id=%s)",
                account_number, name, sm_id,
            )
            return CustomerSyncResult(
                success=True, platform="koios", sm_customer_id=str(sm_id),
            )

        if r.status_code in (502, 504):
            last_error = f"HTTP {r.status_code} (attempt {attempt + 1}/{retries})"
            logger.warning(
                "Koios HTTP %d for %s, attempt %d/%d",
                r.status_code, account_number, attempt + 1, retries,
            )
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        errors = r.json().get("errors", [])
        error_msg = errors[0].get("title", str(errors)) if errors else f"HTTP {r.status_code}"
        details = errors[0].get("details", "") if errors else ""

        # Customer code already exists: find it via pagination fallback
        if "already exists" in str(details).lower() or "already exists" in str(error_msg).lower():
            logger.info(
                "Koios customer %s already exists, searching via pagination",
                account_number,
            )
            existing = _koios_paginated_lookup(account_number, site_code)
            if existing:
                existing_id = existing.get("id", "")
                logger.info(
                    "Found existing Koios customer for %s: %s",
                    account_number, existing_id,
                )
                return CustomerSyncResult(
                    success=True, platform="koios", sm_customer_id=str(existing_id),
                )
            logger.warning(
                "Koios customer %s already exists but could not be found via pagination",
                account_number,
            )

        logger.warning(
            "Koios customer create failed for %s: %s", account_number, error_msg,
        )
        return CustomerSyncResult(success=False, platform="koios", error=error_msg)

    return CustomerSyncResult(
        success=False, platform="koios",
        error=last_error or f"HTTP {last_status}",
    )"""

content = content.replace(old_create, new_create, 1)

# 5. Update attach_koios_meter — retries for 502/504
old_attach = """    body = {"serial": str(meter_serial).strip()}
    url = f"{KOIOS_BASE}/api/v1/customers/{uid}/meter"
    try:
        r = requests.put(
            url,
            json=body,
            headers=_koios_headers(site),
            timeout=API_TIMEOUT,
        )
    except Exception as e:
        logger.error("Koios meter attach PUT failed for %s: %s", account_number, e)
        return CustomerSyncResult(success=False, platform="koios", error=str(e))

    if r.status_code in (200, 201, 204):"""

new_attach = """    body = {"serial": str(meter_serial).strip()}
    url = f"{KOIOS_BASE}/api/v1/customers/{uid}/meter"
    retries = 3
    last_error = None
    for attempt in range(retries):
        try:
            r = requests.put(
                url,
                json=body,
                headers=_koios_headers(site),
                timeout=API_TIMEOUT,
            )
        except Exception as e:
            logger.error(
                "Koios meter attach PUT failed for %s (attempt %d/%d): %s",
                account_number, attempt + 1, retries, e,
            )
            last_error = str(e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)

        if r.status_code in (502, 504):
            logger.warning(
                "Koios meter attach HTTP %d for %s, attempt %d/%d",
                r.status_code, account_number, attempt + 1, retries,
            )
            last_error = f"HTTP {r.status_code}"
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return CustomerSyncResult(success=False, platform="koios", error=last_error)
        break

    if r.status_code in (200, 201, 204):"""

content = content.replace(old_attach, new_attach, 1)

with open(PATH, 'w') as f:
    f.write(content)

print("Patch applied successfully.")
