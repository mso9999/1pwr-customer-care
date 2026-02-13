"""
Contract PDF generator for 1PWR Customer Care Portal.
Ported from the existing Contract Generator (Dropbox-based), adapted for
EC2-native storage.  Uses Jinja2 for template rendering and xhtml2pdf for
HTML-to-PDF conversion.

Contracts are stored on disk at  contracts/{site_code}/{filename}  and served
via the CC Portal API.  SMS delivery uses cutt.ly for URL shortening and
the 1PWR SMS gateway.
"""

import base64
import json
import logging
import os
import re
from datetime import date
from os.path import join
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import jinja2
import requests
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths  (relative to the directory containing this file)
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(_THIS_DIR, "templates")
CONTRACTS_DIR = os.path.join(_THIS_DIR, "contracts")
MEDIA_DIR = os.path.join(_THIS_DIR, "media")

# Jinja2 environment – loads from the templates/ directory
_loader = jinja2.FileSystemLoader(searchpath=TEMPLATES_DIR)
_env = jinja2.Environment(loader=_loader)
TEMPLATE_EN = _env.get_template("template_en.html")
TEMPLATE_SO = _env.get_template("template_so.html")

# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

CUTTLY_TOKEN: Optional[str] = os.environ.get("CUTTLY_TOKEN")
SMS_SERVER_URL: Optional[str] = os.environ.get("SMS_SERVER_URL")
CONTRACT_BASE_URL: str = os.environ.get("CONTRACT_BASE_URL", "https://cc.1pwrafrica.com")

STAFF_NAME = "Matthew Orosz"


# ---------------------------------------------------------------------------
# Staff signature  (loaded once at import time, base64-encoded)
# ---------------------------------------------------------------------------

def _load_staff_signature() -> str:
    """Load the staff signature image and return as base64 string."""
    sig_path = os.path.join(MEDIA_DIR, "Matt_Signature.jpeg")
    if os.path.isfile(sig_path) and os.path.getsize(sig_path) > 0:
        with open(sig_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    # Fallback: transparent 1x1 pixel
    return "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAAAAAAAAf/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AoAB//9k="


_STAFF_SIGNATURE_B64: str = ""
try:
    _STAFF_SIGNATURE_B64 = _load_staff_signature()
except Exception as exc:
    logger.warning("Could not load staff signature: %s", exc)


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def generate_contract(
    *,
    first_name: str,
    last_name: str,
    national_id: str,
    phone_number: str,
    concession: str,
    customer_type: str,
    service_phase: str,
    ampacity: str,
    account_number: str,
    customer_signature_b64: str,
    phone_number_2: str = "",
    email: str = "",
    rate_lsl: Optional[float] = None,
    customer_id: Optional[str] = None,
) -> dict:
    """Generate bilingual contract PDFs and store on disk.

    Returns dict with:
        en_filename, so_filename, en_path, so_path
    """
    # Resolve tariff rate if not explicitly provided
    if rate_lsl is None:
        try:
            from tariff import resolve_rate
            rate_lsl = resolve_rate(
                customer_id=customer_id, concession=concession
            )["rate_lsl"]
        except Exception:
            rate_lsl = 5.0  # fallback

    data = {
        "first_name": first_name,
        "last_name": last_name,
        "national_id": national_id,
        "phone_number": phone_number,
        "concession": concession,
        "customer_type": customer_type,
        "service_phase": service_phase,
        "ampacity": ampacity,
        "account_number": account_number,
        "customer_signature": customer_signature_b64,
        "staff_signature": _STAFF_SIGNATURE_B64,
        "customer_signature_date": date.today().isoformat(),
        "staff_name": STAFF_NAME,
        "phone_number_2": phone_number_2,
        "email": email,
        "rate_lsl": rate_lsl,
    }

    # Render HTML from templates
    html_en = TEMPLATE_EN.render(json_data=data)
    html_so = TEMPLATE_SO.render(json_data=data)

    # Filenames
    safe_last = _safe_name(last_name)
    safe_first = _safe_name(first_name)
    en_filename = f"{account_number}_{safe_last}_{safe_first}_Contract_en.pdf"
    so_filename = f"{account_number}_{safe_last}_{safe_first}_Contract_so.pdf"

    # Ensure output directory exists
    site_dir = os.path.join(CONTRACTS_DIR, concession.upper())
    os.makedirs(site_dir, exist_ok=True)

    en_path = os.path.join(site_dir, en_filename)
    so_path = os.path.join(site_dir, so_filename)

    _html_to_pdf(html_en, en_path)
    _html_to_pdf(html_so, so_path)

    logger.info("Generated contracts: %s, %s", en_path, so_path)

    return {
        "en_filename": en_filename,
        "so_filename": so_filename,
        "en_path": en_path,
        "so_path": so_path,
        "site_code": concession.upper(),
    }


def _html_to_pdf(html_source: str, output_path: str) -> bool:
    """Convert HTML string to PDF file using xhtml2pdf."""
    with open(output_path, "w+b") as f:
        status = pisa.CreatePDF(src=html_source, dest=f)
    if status.err:
        logger.error("xhtml2pdf error for %s: %d errors", output_path, status.err)
    return not status.err


def _safe_name(name: str) -> str:
    """Sanitize a name for use in filenames."""
    return re.sub(r"[^\w\-]", "", name.strip().replace(" ", "_"))


# ---------------------------------------------------------------------------
# Public download URL construction
# ---------------------------------------------------------------------------

def build_download_url(site_code: str, filename: str) -> str:
    """Build the public URL for downloading a contract."""
    return f"{CONTRACT_BASE_URL}/api/contracts/download/{site_code}/{filename}"


# ---------------------------------------------------------------------------
# URL shortening (cutt.ly)
# ---------------------------------------------------------------------------

def shorten_url(full_link: str) -> str:
    """Shorten a URL using the cutt.ly API.  Falls back to the original URL."""
    if not CUTTLY_TOKEN:
        logger.warning("CUTTLY_TOKEN not set – returning full URL")
        return full_link
    try:
        encoded = quote(full_link)
        r = requests.get(
            f"http://cutt.ly/api/api.php?key={CUTTLY_TOKEN}&short={encoded}",
            timeout=10,
        )
        result = json.loads(r.text)["url"]
        if result.get("status") == 7:
            return result["shortLink"]
        logger.warning("cutt.ly status %s – returning full URL", result.get("status"))
    except Exception as exc:
        logger.warning("cutt.ly failed: %s – returning full URL", exc)
    return full_link


# ---------------------------------------------------------------------------
# SMS delivery
# ---------------------------------------------------------------------------

def _add_les_dialing_code(phone_number: str) -> str:
    """Ensure Lesotho dialing code (266) is prepended."""
    phone_number = str(phone_number).strip()
    if not phone_number:
        return phone_number
    if "266" in phone_number:
        return phone_number
    if phone_number.startswith("+"):
        return phone_number[1:]
    if len(phone_number) == 8:
        return f"266{phone_number}"
    return phone_number


def send_contract_sms(
    *,
    first_name: str,
    last_name: str,
    phone_number: str,
    en_url: str,
    so_url: str,
) -> bool:
    """Send the contract download links to the customer via SMS.

    Sends a Sesotho message (primary language) with the Sesotho link.
    Returns True if the SMS was dispatched successfully.
    """
    if not SMS_SERVER_URL:
        logger.warning("SMS_SERVER_URL not set – skipping SMS")
        return False

    short_so = shorten_url(so_url)

    message = (
        f"Lumela {first_name} {last_name}. Rea leboha ha u ngolisitse le One Power. "
        f"Fumana konteraka ea hau eo u e saenneng mona: {short_so}. "
        f"Hore u e bale u lokeloa ho e bula ka internet."
    )

    number = _add_les_dialing_code(phone_number)
    url = (
        f"{SMS_SERVER_URL}/generate_and_send.php"
        f"?message={quote(message)}&type=welcome&number={number}"
    )
    try:
        requests.get(url, timeout=15)
        logger.info("SMS sent to %s", number)
        return True
    except Exception as exc:
        logger.error("SMS send failed for %s: %s", number, exc)
        return False


# ---------------------------------------------------------------------------
# Contract file listing (for customer detail page)
# ---------------------------------------------------------------------------

def list_customer_contracts(account_number: str) -> list[dict]:
    """List all contract files on disk for a given account number.

    Returns list of dicts with: filename, lang, site_code, path, url
    """
    results = []
    if not os.path.isdir(CONTRACTS_DIR):
        return results

    prefix = account_number.upper() + "_"
    for site_dir_name in os.listdir(CONTRACTS_DIR):
        site_path = os.path.join(CONTRACTS_DIR, site_dir_name)
        if not os.path.isdir(site_path):
            continue
        for fname in os.listdir(site_path):
            if fname.upper().startswith(prefix) and fname.lower().endswith(".pdf"):
                lang = "so" if "_Contract_so.pdf" in fname else "en"
                results.append({
                    "filename": fname,
                    "lang": lang,
                    "site_code": site_dir_name,
                    "path": os.path.join(site_path, fname),
                    "url": build_download_url(site_dir_name, fname),
                })
    return results
