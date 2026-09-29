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
from typing import Optional, Tuple
from urllib.parse import quote
import jinja2
import requests

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

# Lazy-load commission HTML templates so a missing/broken template directory
# cannot prevent the entire FastAPI app (including /api/config, /api/health)
# from importing. PDF generation still fails fast if templates are absent.
_cached_commission_en: Optional[jinja2.Template] = None
_cached_commission_so: Optional[jinja2.Template] = None


def _commission_contract_templates() -> Tuple[jinja2.Template, jinja2.Template]:
    global _cached_commission_en, _cached_commission_so
    if _cached_commission_en is None:
        _cached_commission_en = _env.get_template("template_en.html")
        _cached_commission_so = _env.get_template("template_so.html")
    return _cached_commission_en, _cached_commission_so

# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

CUTTLY_TOKEN: Optional[str] = os.environ.get("CUTTLY_TOKEN")
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

def _active_country_code() -> str:
    from country_config import COUNTRY
    return (getattr(COUNTRY, "code", None) or "LS").upper()


def _signature_b64(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    return text


def _load_logo_b64() -> str:
    path = os.path.join(MEDIA_DIR, "1pwr-logo.png")
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            return base64.b64encode(handle.read()).decode("ascii")
    return ""


def _format_xof(rate: float) -> str:
    value = float(rate)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _benin_tariff(concession: str, customer_id: Optional[str]) -> float:
    """Site tariff for a Benin contract.

    Customer and concession overrides win. Otherwise use the Benin default
    (160 XOF/kWh). The shared resolver's global fallback is the Lesotho rate.
    """
    from country_config import BENIN
    rate = float(BENIN.default_tariff_rate or 160)
    try:
        from tariff import resolve_rate
        resolved = resolve_rate(customer_id=customer_id, concession=concession)
        if resolved.get("source") in ("customer", "concession"):
            rate = float(resolved["rate_lsl"])
    except Exception:
        logger.warning("Benin tariff lookup failed; using %s XOF/kWh", rate)
    return rate


def _benin_place(concession: str) -> tuple[str, str]:
    from country_config import BENIN
    code = (concession or "").strip().upper()
    locality = BENIN.site_abbrev.get(code) or (concession or "")
    commune = BENIN.site_districts.get(code, "")
    return locality, commune


def _phase_label(service_phase: str) -> str:
    labels = {"single": "Monophasé", "three": "Triphasé"}
    text = (service_phase or "").strip()
    return labels.get(text.lower(), text)


def _format_fr_date(iso_day: str) -> str:
    text = (iso_day or "").strip()
    try:
        year, month, day = text.split("-")
        return f"{day}/{month}/{year}"
    except ValueError:
        return text


_cached_commission_fr: Optional[jinja2.Template] = None


def _french_contract_template() -> jinja2.Template:
    global _cached_commission_fr
    if _cached_commission_fr is None:
        _cached_commission_fr = _env.get_template("template_bj.html")
    return _cached_commission_fr


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
    connection_date: str = "",
) -> dict:
    """Generate the commissioning contract PDF(s) and store them on disk.

    Lesotho: English and Sesotho. Benin: one French Mionwa Generation contract.
    """
    if _active_country_code() == "BN":
        return _generate_contract_fr(
            first_name=first_name,
            last_name=last_name,
            national_id=national_id,
            phone_number=phone_number,
            concession=concession,
            customer_type=customer_type,
            service_phase=service_phase,
            ampacity=ampacity,
            account_number=account_number,
            customer_signature_b64=customer_signature_b64,
            customer_id=customer_id,
            connection_date=connection_date,
        )
    return _generate_contract_ls(
        first_name=first_name,
        last_name=last_name,
        national_id=national_id,
        phone_number=phone_number,
        concession=concession,
        customer_type=customer_type,
        service_phase=service_phase,
        ampacity=ampacity,
        account_number=account_number,
        customer_signature_b64=customer_signature_b64,
        phone_number_2=phone_number_2,
        email=email,
        rate_lsl=rate_lsl,
        customer_id=customer_id,
    )


def _generate_contract_ls(
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
    """Generate bilingual Lesotho contract PDFs."""
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
        "customer_signature": _signature_b64(customer_signature_b64),
        "staff_signature": _STAFF_SIGNATURE_B64,
        "customer_signature_date": date.today().isoformat(),
        "staff_name": STAFF_NAME,
        "phone_number_2": phone_number_2,
        "email": email,
        "rate_lsl": rate_lsl,
    }

    tmpl_en, tmpl_so = _commission_contract_templates()
    html_en = tmpl_en.render(json_data=data)
    html_so = tmpl_so.render(json_data=data)

    safe_last = _safe_name(last_name)
    safe_first = _safe_name(first_name)
    en_filename = f"{account_number}_{safe_last}_{safe_first}_Contract_en.pdf"
    so_filename = f"{account_number}_{safe_last}_{safe_first}_Contract_so.pdf"

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


def _generate_contract_fr(
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
    customer_id: Optional[str] = None,
    connection_date: str = "",
) -> dict:
    """Generate the French Mionwa Generation subscription contract."""
    locality, commune = _benin_place(concession)
    signed = date.today().isoformat()
    data = {
        "first_name": first_name,
        "last_name": last_name,
        "national_id": national_id,
        "phone_number": phone_number,
        "concession": concession,
        "locality": locality,
        "commune": commune,
        "customer_type": customer_type,
        "service_phase_label": _phase_label(service_phase),
        "ampacity": ampacity,
        "account_number": account_number,
        "connection_date": _format_fr_date(connection_date),
        "customer_signature": _signature_b64(customer_signature_b64),
        "customer_signature_date": _format_fr_date(signed),
        "rate_xof": _format_xof(_benin_tariff(concession, customer_id)),
        "logo_b64": _load_logo_b64(),
    }

    html_fr = _french_contract_template().render(json_data=data)
    safe_last = _safe_name(last_name)
    safe_first = _safe_name(first_name)
    fr_filename = f"{account_number}_{safe_last}_{safe_first}_Contract_fr.pdf"
    site_dir = os.path.join(CONTRACTS_DIR, (concession or "BN").upper())
    os.makedirs(site_dir, exist_ok=True)
    fr_path = os.path.join(site_dir, fr_filename)
    if not _html_to_pdf(html_fr, fr_path):
        raise RuntimeError("French contract PDF was not written")

    logger.info("Generated French contract: %s", fr_path)
    return {
        "fr_filename": fr_filename,
        "fr_path": fr_path,
        "site_code": (concession or "BN").upper(),
    }


def _html_to_pdf(html_source: str, output_path: str) -> bool:
    """Convert HTML string to PDF file using xhtml2pdf.

    Import xhtml2pdf lazily so a missing/broken native stack (e.g. pycairo)
    cannot prevent the entire FastAPI app from loading; commission/financing
    routers import this module at startup.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:
        logger.error("xhtml2pdf import failed (PDF generation unavailable): %s", exc)
        return False
    with open(output_path, "w+b") as f:
        status = pisa.CreatePDF(src=html_source, dest=f, encoding="utf-8")
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

def send_contract_sms(
    *,
    first_name: str,
    last_name: str,
    phone_number: str,
    en_url: str = "",
    so_url: str = "",
    fr_url: str = "",
    account_number: str | None = None,
) -> bool:
    """Send the contract download link to the customer via SMS.

    Lesotho: Sesotho message with the Sesotho link.
    Benin: French message with the French link, on this process's SMS gateway.
    Returns True if the SMS was dispatched successfully.
    """
    from sms_outbound import send_gateway_sms

    if _active_country_code() == "BN":
        short = shorten_url(fr_url) if fr_url else ""
        message = (
            f"Bonjour {first_name} {last_name}. Merci pour votre abonnement chez "
            f"Mionwa Generation. Votre contrat signé est disponible ici : {short}. "
            f"Une connexion internet est nécessaire pour l'ouvrir."
        )
        contract_url = short
    else:
        short_so = shorten_url(so_url)
        message = (
            f"Lumela {first_name} {last_name}. Rea leboha ha u ngolisitse le One Power. "
            f"Fumana konteraka ea hau eo u e saenneng mona: {short_so}. "
            f"Hore u e bale u lokeloa ho e bula ka internet."
        )
        contract_url = short_so

    ok = send_gateway_sms(phone_number, message, sms_type="welcome",
                          trigger="contract")
    if ok and account_number:
        try:
            from app_notifications import mirror_to_app
            mirror_to_app(
                account_number,
                "welcome",
                "1PWR",
                message,
                {"contract_url": contract_url},
            )
        except Exception:  # noqa: BLE001
            pass
    return ok


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
                if "_Contract_fr.pdf" in fname:
                    lang = "fr"
                elif "_Contract_so.pdf" in fname:
                    lang = "so"
                else:
                    lang = "en"
                results.append({
                    "filename": fname,
                    "lang": lang,
                    "site_code": site_dir_name,
                    "path": os.path.join(site_path, fname),
                    "url": build_download_url(site_dir_name, fname),
                })
    return results
