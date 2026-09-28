"""
Operator-editable SMS formats for the pass-through SMS gateways.

The Lesotho and Benin gateways forward every SMS unchanged to
``POST /api/sms/incoming``; CC decides what each message is:

1. **Payment confirmation** — enabled ``sms_formats`` rows (kind ``payment``)
   are tried in priority order, then the built-in parsers (``mpesa_sms`` /
   ``momo_bj``). A new provider (Moov Money, Celtiis Cash, …) or a changed
   template is added from the **SMS Formats** page, not a code deploy.
2. **Balance request** — enabled rows of kind ``balance_request`` (e.g.
   ``Balance {account}``); CC replies itself when
   ``sms_balance_replies_enabled`` is on.

A format is either a *template* (``Paiement {amount}F de {*} Message:{account}``)
or a raw Python regex with the same named groups.

API (``/api/admin/sms-formats``, Admin / IT / O&M — see ``require_sms_format_editor``):
    GET    ""                 list formats, built-ins, settings
    POST   ""                 create
    PUT    /{id}              update
    DELETE /{id}              delete
    POST   /test              run one SMS through a draft format and the live pipeline
    POST   /preview           compare recent inbound SMS before/after a draft change
    GET    /settings, PUT /settings
    GET    /senders           observed senders (to build the trusted list)
    GET    /unprocessed       unparsed / unmatched SMS, with "would parse now"
    POST   /replay            re-run selected unprocessed SMS through the pipeline
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from country_config import COUNTRY
from customer_api import get_connection
from middleware import effective_roles, raise_privilege_denied, require_employee
from models import CCRole, CurrentUser

logger = logging.getLogger("cc-api.sms-formats")

KINDS = ("payment", "balance_request")
PATTERN_TYPES = ("template", "regex")
PAYMENT_GROUPS = {"amount", "account", "phone", "txn_id", "remark"}
BALANCE_GROUPS = {"account"}
MAX_PATTERN_LEN = 2000
MAX_CONTENT_LEN = 2000
CACHE_TTL_S = 30.0
SENDER_MODES = ("off", "warn", "enforce")

# IS&T departments map to onm_team (db_auth._IT_DEPT_MAPPINGS), so these two
# roles are "Admin / IT / O&M". Nexus administer_cc holders are admins too.
EDITOR_ROLES = (CCRole.superadmin.value, CCRole.onm_team.value)


def require_sms_format_editor(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
    if "administer_cc" in (user.privilege_actions or []):
        return user
    if set(effective_roles(user)).intersection(EDITOR_ROLES):
        return user
    raise_privilege_denied(user, EDITOR_ROLES, "manage SMS formats")
    return user


def require_sms_inbox_reader(user: CurrentUser = Depends(require_employee)) -> CurrentUser:
    """Inbox viewers: customer-care operators plus the existing format editors.

    Finance and a direct-login employee with neither ``operate_customer_care``
    nor an editor role stay denied. Replay and format settings stay on
    ``require_sms_format_editor``.
    """
    actions = set(user.privilege_actions or [])
    if actions.intersection({"operate_customer_care", "administer_cc"}):
        return user
    if set(effective_roles(user)).intersection(EDITOR_ROLES):
        return user
    raise_privilege_denied(user, EDITOR_ROLES, "view the SMS inbox")
    return user


def benin_replay_credit_enabled() -> bool:
    """Benin replay must not follow ``meter_credit_enabled`` (that defaults on)."""
    return os.environ.get("SMS_BN_REPLAY_CREDIT_ENABLED", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{(\*|[a-z_]+)\}")
_TEMPLATE_GROUPS = {
    "amount": r"\d[\d \u00a0.,]*\d|\d",
    "account": r"\d{3,4}\s*[A-Za-z]{2,4}",
    "phone": r"\+?\d[\d \u00a0\-]{6,18}\d",
    "txn_id": r"[A-Za-z0-9][A-Za-z0-9.\-_]{2,40}",
    "remark": None,
}


def template_to_regex(template: str) -> str:
    """Convert ``Paiement {amount}F de {*} ID:{txn_id}`` to a regex.

    Literal text matches case-insensitively with flexible whitespace;
    ``{*}`` skips any text. Each named placeholder may appear once.
    """
    parts = _PLACEHOLDER_RE.split(template or "")
    out: list[str] = []
    seen: set[str] = set()
    for i, part in enumerate(parts):
        if i % 2 == 0:
            for tok in re.split(r"(\s+)", part):
                if not tok:
                    continue
                out.append(r"\s+" if tok.isspace() else re.escape(tok))
            continue
        name = part
        is_last = i == len(parts) - 2 and not parts[-1].strip()
        if name == "*":
            out.append(r"[^\n]*" if is_last else r".*?")
            continue
        if name not in _TEMPLATE_GROUPS:
            raise ValueError(
                f"Unknown placeholder {{{name}}}. Use {{amount}}, {{account}}, {{phone}}, "
                "{txn_id}, {remark} or {*}."
            )
        if name in seen:
            raise ValueError(f"Placeholder {{{name}}} is used more than once.")
        seen.add(name)
        body = _TEMPLATE_GROUPS[name] or (r"[^\n]*" if is_last else r".*?")
        out.append(f"(?P<{name}>{body})")
    return "".join(out)


def compile_pattern(pattern: str, pattern_type: str, kind: str) -> re.Pattern:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if pattern_type not in PATTERN_TYPES:
        raise ValueError(f"pattern_type must be one of {', '.join(PATTERN_TYPES)}")
    if not (pattern or "").strip():
        raise ValueError("Pattern is empty.")
    if len(pattern) > MAX_PATTERN_LEN:
        raise ValueError(f"Pattern is longer than {MAX_PATTERN_LEN} characters.")
    source = template_to_regex(pattern) if pattern_type == "template" else pattern
    try:
        rx = re.compile(source, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise ValueError(f"Invalid pattern: {exc}") from exc
    groups = set(rx.groupindex)
    allowed = PAYMENT_GROUPS if kind == "payment" else BALANCE_GROUPS
    unknown = groups - allowed
    if unknown:
        raise ValueError(
            f"Unknown field(s) {', '.join(sorted(unknown))}; allowed: {', '.join(sorted(allowed))}."
        )
    if kind == "payment" and "amount" not in groups:
        raise ValueError("A payment format must capture {amount}.")
    return rx


def compile_sender(sender_pattern: Optional[str]) -> Optional[re.Pattern]:
    s = (sender_pattern or "").strip()
    if not s:
        return None
    if len(s) > 300:
        raise ValueError("Sender pattern is longer than 300 characters.")
    try:
        return re.compile(s, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid sender pattern: {exc}") from exc


# ---------------------------------------------------------------------------
# Applying formats
# ---------------------------------------------------------------------------

def _digits(value: Any) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def normalize_amount(raw: Optional[str], decimal_separator: str = ".") -> Optional[float]:
    s = re.sub(r"[^\d.,]", "", (raw or "").replace("\u00a0", ""))
    if not s:
        return None
    if decimal_separator == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_phone(raw: Optional[str], prefix: Optional[str] = None) -> str:
    d = _digits(raw)
    if d.startswith("00"):
        d = d[2:]
    p = _digits(prefix) or _digits(COUNTRY.dial_code)
    if d and p and not d.startswith(p) and len(d) <= 10:
        d = p + d
    return d


def default_decimal_separator() -> str:
    return "," if COUNTRY.currency == "XOF" else "."


def build_format(row: dict) -> dict:
    """Compile a DB/draft row into a runtime format (raises ValueError)."""
    fmt = dict(row)
    fmt.setdefault("id", None)
    fmt.setdefault("kind", "payment")
    fmt.setdefault("pattern_type", "template")
    fmt.setdefault("decimal_separator", default_decimal_separator())
    fmt["rx"] = compile_pattern(fmt["pattern"], fmt["pattern_type"], fmt["kind"])
    fmt["sender_rx"] = compile_sender(fmt.get("sender_pattern"))
    return fmt


def apply_format(fmt: dict, content: str, sender: str = "") -> Optional[dict[str, Any]]:
    content = (content or "")[:MAX_CONTENT_LEN]
    if fmt.get("sender_rx") is not None and not fmt["sender_rx"].search(sender or ""):
        return None
    m = fmt["rx"].search(content)
    if not m:
        return None
    g = {k: (v or "") for k, v in m.groupdict().items()}
    account = " ".join(g.get("account", "").split())
    meta = {
        "provider": fmt.get("provider") or "custom",
        "format_id": fmt.get("id"),
        "format_name": fmt.get("name") or "draft",
        "sender_checked": fmt.get("sender_rx") is not None,
    }
    if fmt["kind"] == "balance_request":
        return {"kind": "balance_request", "account_hint": account, **meta}
    amount = normalize_amount(g.get("amount"), fmt.get("decimal_separator") or ".")
    if amount is None or amount <= 0:
        return None
    txn_id = g.get("txn_id", "").strip()
    remark = " ".join(g.get("remark", "").split())
    return {
        "txn_id": txn_id,
        "amount": amount,
        "phone": normalize_phone(g["phone"], fmt.get("phone_prefix")) if g.get("phone") else "",
        "reference": txn_id,
        "remark_raw": remark or account,
        "account_hint": account,
        **meta,
    }


# ---------------------------------------------------------------------------
# Active format cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "formats": None}

_COLUMNS = (
    "id, country_code, kind, provider, name, pattern_type, pattern, sender_pattern, "
    "decimal_separator, phone_prefix, priority, enabled, samples, notes, "
    "created_by, created_at, updated_by, updated_at"
)


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["formats"] = None


def _load_active() -> list[dict]:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {_COLUMNS} FROM sms_formats "
                "WHERE enabled AND country_code = %s ORDER BY priority, id",
                (COUNTRY.code,),
            )
            rows = _rows(cur)
    except Exception as exc:
        logger.debug("sms_formats not loaded (%s); using built-in parsers only", exc)
        return []
    out = []
    for row in rows:
        try:
            out.append(build_format(row))
        except ValueError as exc:
            logger.warning("SMS format %s (%s) skipped: %s", row.get("id"), row.get("name"), exc)
    return out


def active_formats(kind: str) -> list[dict]:
    now = time.monotonic()
    with _cache_lock:
        formats = _cache["formats"]
        fresh = formats is not None and now - _cache["at"] < CACHE_TTL_S
    if not fresh:
        formats = _load_active()
        with _cache_lock:
            _cache["formats"] = formats
            _cache["at"] = now
    return [f for f in formats if f["kind"] == kind]


def parse_payment(
    content: str,
    sender: str,
    builtin: Callable[[str, str], Optional[dict]],
    formats: Optional[Iterable[dict]] = None,
) -> Optional[dict[str, Any]]:
    """Operator formats first (priority order), then the built-in parser."""
    for fmt in (active_formats("payment") if formats is None else formats):
        try:
            parsed = apply_format(fmt, content, sender)
        except Exception:
            logger.exception("SMS format %s failed", fmt.get("id"))
            continue
        if parsed:
            return parsed
    parsed = builtin(content, sender)
    if parsed:
        parsed = dict(parsed)
        parsed.setdefault("format_name", "built-in")
    return parsed


def match_balance_request(
    content: str, sender: str, formats: Optional[Iterable[dict]] = None,
) -> Optional[dict[str, Any]]:
    for fmt in (active_formats("balance_request") if formats is None else formats):
        try:
            hit = apply_format(fmt, content, sender)
        except Exception:
            logger.exception("SMS balance format %s failed", fmt.get("id"))
            continue
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# Settings (system_config)
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATES = {
    "LS": {
        "sms_balance_reply_template": "Ntlo ea {account} e salletsoe ke: M{balance_currency} ({balance_kwh} kWh).",
        "sms_balance_unregistered_template": "Nomoro ea hau ha ea ngolisoa le OnePower.",
    },
    "BN": {
        "sms_balance_reply_template": "Compte {account} : solde {balance_currency} FCFA ({balance_kwh} kWh).",
        "sms_balance_unregistered_template": "Votre numéro n'est pas enregistré chez 1PWR.",
    },
}
_SETTING_KEYS = (
    "sms_trusted_senders",
    "sms_trusted_senders_mode",
    "sms_balance_replies_enabled",
    "sms_balance_reply_template",
    "sms_balance_unregistered_template",
)


def get_settings(conn) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM system_config WHERE key = ANY(%s)", (list(_SETTING_KEYS),))
    raw = {k: v for k, v in cur.fetchall()}
    defaults = _DEFAULT_TEMPLATES.get(COUNTRY.code, _DEFAULT_TEMPLATES["LS"])
    mode = (raw.get("sms_trusted_senders_mode") or "off").strip().lower()
    return {
        "trusted_senders": [
            s.strip() for s in re.split(r"[\n,]", raw.get("sms_trusted_senders") or "") if s.strip()
        ],
        "trusted_senders_mode": mode if mode in SENDER_MODES else "off",
        "balance_replies_enabled": (raw.get("sms_balance_replies_enabled") or "0").strip() in ("1", "true", "yes"),
        "balance_reply_template": raw.get("sms_balance_reply_template") or defaults["sms_balance_reply_template"],
        "balance_unregistered_template": (
            raw.get("sms_balance_unregistered_template") or defaults["sms_balance_unregistered_template"]
        ),
    }


def _norm_sender(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def sender_is_trusted(settings: dict, sender: str, parsed: Optional[dict] = None) -> bool:
    if parsed and parsed.get("sender_checked"):
        return True
    wanted = _norm_sender(sender)
    wanted_digits = _digits(sender)
    for entry in settings.get("trusted_senders") or []:
        e = _norm_sender(entry)
        if e == wanted or (_digits(entry) and _digits(entry) == wanted_digits and len(wanted_digits) <= 6):
            return True
    return False


def sender_check(conn, sender: str, parsed: Optional[dict]) -> str:
    """Return 'ok', 'warn' (untrusted but allowed) or 'reject'."""
    try:
        settings = get_settings(conn)
    except Exception:
        conn.rollback()
        return "ok"
    mode = settings["trusted_senders_mode"]
    if mode == "off" or sender_is_trusted(settings, sender, parsed):
        return "ok"
    return "reject" if mode == "enforce" else "warn"


# ---------------------------------------------------------------------------
# Balance replies (replaces the PHP balance-request file watcher)
# ---------------------------------------------------------------------------

def _fmt_number(value: Any) -> str:
    try:
        f = round(float(value), 2)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else f"{f:.2f}"


def render_balance_reply(template: str, payload: dict) -> str:
    values = {
        "account": payload.get("account_number", ""),
        "balance_currency": _fmt_number(payload.get("balance_currency")),
        "balance_kwh": _fmt_number(payload.get("balance_kwh")),
    }
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def handle_balance_request(match: dict, sender: str) -> str:
    """Send the balance SMS for a matched balance request. Returns an outcome label."""
    from fastapi import HTTPException as _HTTPException

    from mpesa_sms import account_exists, account_hint_candidates
    from payments import _account_numbers_for_phone, _balance_payload_for_conn
    from sms_gateway_balance_rate import (
        balance_gateway_rate_key_for_account,
        balance_gateway_rate_key_for_phone,
        enforce_balance_gateway_rate_limit,
        record_balance_gateway_request,
    )
    from sms_outbound import send_gateway_sms

    with get_connection() as conn:
        settings = get_settings(conn)
        accounts: list[str] = []
        for cand in account_hint_candidates(match):
            if account_exists(conn, cand):
                accounts = [cand]
                break
        rate_key = (
            balance_gateway_rate_key_for_account(accounts[0]) if accounts
            else balance_gateway_rate_key_for_phone(sender)
        )
        try:
            enforce_balance_gateway_rate_limit(conn, rate_key)
        except _HTTPException:
            return "balance_rate_limited"
        if not accounts and not match.get("account_hint"):
            accounts = _account_numbers_for_phone(conn, sender)
        messages = [
            render_balance_reply(settings["balance_reply_template"], _balance_payload_for_conn(conn, acct))
            for acct in accounts
        ]
        record_balance_gateway_request(conn, rate_key)
        conn.commit()

    if not messages:
        send_gateway_sms(sender, settings["balance_unregistered_template"], sms_type="balance",
                         trigger="sms_balance_request")
        return "balance_unregistered"
    for acct, text in zip(accounts, messages):
        send_gateway_sms(sender, text, sms_type="balance", account_number=acct,
                         trigger="sms_balance_request")
    return "balance_replied"


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/admin/sms-formats", tags=["sms-formats"])


class Sample(BaseModel):
    text: str = Field(..., max_length=MAX_CONTENT_LEN)
    sender: str = ""
    expect_amount: Optional[float] = None
    expect_account: Optional[str] = None
    expect_txn_id: Optional[str] = None


class FormatIn(BaseModel):
    kind: str = "payment"
    provider: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=120)
    pattern_type: str = "template"
    pattern: str = Field(..., min_length=1, max_length=MAX_PATTERN_LEN)
    sender_pattern: Optional[str] = None
    decimal_separator: Optional[str] = None
    phone_prefix: Optional[str] = None
    priority: int = 100
    enabled: bool = False
    samples: list[Sample] = Field(default_factory=list)
    notes: Optional[str] = None


class DraftIn(FormatIn):
    id: Optional[int] = None


class TestIn(BaseModel):
    draft: Optional[DraftIn] = None
    text: str = Field(..., max_length=MAX_CONTENT_LEN)
    sender: str = ""


class PreviewIn(BaseModel):
    draft: Optional[DraftIn] = None
    delete_id: Optional[int] = None
    days: int = Field(30, ge=1, le=180)
    limit: int = Field(1000, ge=1, le=5000)


class SettingsIn(BaseModel):
    trusted_senders: Optional[list[str]] = None
    trusted_senders_mode: Optional[str] = None
    balance_replies_enabled: Optional[bool] = None
    balance_reply_template: Optional[str] = Field(None, max_length=320)
    balance_unregistered_template: Optional[str] = Field(None, max_length=320)


class ReplayIn(BaseModel):
    log_ids: list[int] = Field(..., min_length=1, max_length=200)
    allow_possible_duplicates: bool = False


def _draft_row(body: FormatIn, fmt_id: Optional[int] = None) -> dict:
    row = body.model_dump()
    row["id"] = fmt_id
    row["provider"] = row["provider"].strip()
    row["name"] = row["name"].strip()
    row["decimal_separator"] = row.get("decimal_separator") or default_decimal_separator()
    if row["decimal_separator"] not in (".", ","):
        raise HTTPException(400, "decimal_separator must be '.' or ','")
    row["samples"] = [s if isinstance(s, dict) else s.model_dump() for s in row["samples"]]
    return row


def _compile_or_400(row: dict) -> dict:
    try:
        return build_format(row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _builtin_parser() -> Callable[[str, str], Optional[dict]]:
    from ingest import _builtin_gateway_payment

    return _builtin_gateway_payment


def check_samples(fmt: dict) -> list[dict]:
    """Run a format against its own samples; returns per-sample results."""
    results = []
    for s in fmt.get("samples") or []:
        hit = apply_format(fmt, s.get("text", ""), s.get("sender", ""))
        problems = []
        if not hit:
            problems.append("did not match")
        elif fmt["kind"] == "payment":
            if s.get("expect_amount") is not None and abs(hit["amount"] - float(s["expect_amount"])) > 0.001:
                problems.append(f"amount {hit['amount']} != expected {s['expect_amount']}")
            if s.get("expect_txn_id") and hit["txn_id"] != s["expect_txn_id"]:
                problems.append(f"txn_id {hit['txn_id']!r} != expected {s['expect_txn_id']!r}")
        if hit and s.get("expect_account"):
            got = hit.get("account_hint", "").replace(" ", "").upper()
            if got != s["expect_account"].replace(" ", "").upper():
                problems.append(f"account {got!r} != expected {s['expect_account']!r}")
        results.append({"text": s.get("text", ""), "result": _public(hit), "ok": not problems,
                         "problems": problems})
    return results


def _public(parsed: Optional[dict]) -> Optional[dict]:
    if not parsed:
        return None
    return {k: v for k, v in parsed.items() if k not in ("rx", "sender_rx")}


def _public_format(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("rx", "sender_rx")}
    for k in ("created_at", "updated_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


def _audit(cur, format_id, action, actor, before, after) -> None:
    cur.execute(
        "INSERT INTO sms_format_audit (format_id, action, actor, before, after) VALUES (%s,%s,%s,%s,%s)",
        (format_id, action, actor,
         json.dumps(_public_format(before), default=str) if before else None,
         json.dumps(_public_format(after), default=str) if after else None),
    )


def _get_row(cur, fmt_id: int) -> dict:
    cur.execute(f"SELECT {_COLUMNS} FROM sms_formats WHERE id = %s AND country_code = %s",
                (fmt_id, COUNTRY.code))
    rows = _rows(cur)
    if not rows:
        raise HTTPException(404, "SMS format not found")
    return rows[0]


def _all_rows(cur) -> list[dict]:
    cur.execute(f"SELECT {_COLUMNS} FROM sms_formats WHERE country_code = %s ORDER BY kind, priority, id",
                (COUNTRY.code,))
    return _rows(cur)


def _actor(user: CurrentUser) -> str:
    return f"{user.user_id} ({user.name})" if user.name else user.user_id


BUILTINS = {
    "LS": [
        {"provider": "mpesa", "name": "M-Pesa confirmation (code)",
         "example": "ABC123 Confirmed. on 1/9/26 at 10:00 AM M50.00 received from 26657000000 ... Remark: 0123MAK"},
        {"provider": "ecocash", "name": "EcoCash (code, sender 199)",
         "example": "You have received M25 from Name-62205631 for 0118mat. Approval Code: MP260416..."},
    ],
    "BN": [
        {"provider": "momo_bj", "name": "MTN MoMo (code)",
         "example": "Paiement 10F de NAME (2290197313991) 2026-09-27 18:43:37 Message:0001KOT Solde:10563519F ID:12985"},
    ],
}


@router.get("")
def list_formats(user: CurrentUser = Depends(require_sms_format_editor)):
    with get_connection() as conn:
        cur = conn.cursor()
        rows = _all_rows(cur)
        settings = get_settings(conn)
    formats = []
    for row in rows:
        entry = _public_format(row)
        try:
            entry["sample_results"] = check_samples(build_format(row))
            entry["compile_error"] = None
        except ValueError as exc:
            entry["sample_results"] = []
            entry["compile_error"] = str(exc)
        formats.append(entry)
    return {
        "country_code": COUNTRY.code,
        "currency": COUNTRY.currency,
        "dial_code": COUNTRY.dial_code,
        "default_decimal_separator": default_decimal_separator(),
        "formats": formats,
        "builtins": BUILTINS.get(COUNTRY.code, []),
        "settings": settings,
    }


# Registered before the /{fmt_id} routes so "settings" is not read as an id.
@router.get("/settings")
def read_settings(user: CurrentUser = Depends(require_sms_format_editor)):
    with get_connection() as conn:
        return get_settings(conn)


@router.put("/settings")
def put_settings(body: SettingsIn, user: CurrentUser = Depends(require_sms_format_editor)):
    return write_settings(body, user)


def _validate_for_save(fmt: dict) -> None:
    results = check_samples(fmt)
    if fmt["enabled"]:
        if not results:
            raise HTTPException(400, "Add at least one sample SMS before enabling a format.")
        bad = [r for r in results if not r["ok"]]
        if bad:
            raise HTTPException(400, "Sample check failed: " + "; ".join(
                f"{r['text'][:40]}… {', '.join(r['problems'])}" for r in bad))


@router.post("")
def create_format(body: FormatIn, user: CurrentUser = Depends(require_sms_format_editor)):
    row = _draft_row(body)
    fmt = _compile_or_400(row)
    _validate_for_save(fmt)
    actor = _actor(user)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO sms_formats (country_code, kind, provider, name, pattern_type, pattern,
                   sender_pattern, decimal_separator, phone_prefix, priority, enabled, samples, notes,
                   created_by, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (COUNTRY.code, row["kind"], row["provider"], row["name"], row["pattern_type"], row["pattern"],
             row.get("sender_pattern") or None, row["decimal_separator"], row.get("phone_prefix") or None,
             row["priority"], row["enabled"], json.dumps(row["samples"]), row.get("notes"), actor, actor),
        )
        new_id = cur.fetchone()[0]
        saved = _get_row(cur, new_id)
        _audit(cur, new_id, "create", actor, None, saved)
        conn.commit()
    invalidate_cache()
    logger.info("SMS format %s created by %s: %s", new_id, actor, row["name"])
    return _public_format(saved)


@router.put("/{fmt_id}")
def update_format(fmt_id: int, body: FormatIn, user: CurrentUser = Depends(require_sms_format_editor)):
    row = _draft_row(body, fmt_id)
    fmt = _compile_or_400(row)
    _validate_for_save(fmt)
    actor = _actor(user)
    with get_connection() as conn:
        cur = conn.cursor()
        before = _get_row(cur, fmt_id)
        cur.execute(
            """UPDATE sms_formats SET kind=%s, provider=%s, name=%s, pattern_type=%s, pattern=%s,
                   sender_pattern=%s, decimal_separator=%s, phone_prefix=%s, priority=%s, enabled=%s,
                   samples=%s, notes=%s, updated_by=%s, updated_at=NOW()
               WHERE id=%s AND country_code=%s""",
            (row["kind"], row["provider"], row["name"], row["pattern_type"], row["pattern"],
             row.get("sender_pattern") or None, row["decimal_separator"], row.get("phone_prefix") or None,
             row["priority"], row["enabled"], json.dumps(row["samples"]), row.get("notes"), actor,
             fmt_id, COUNTRY.code),
        )
        after = _get_row(cur, fmt_id)
        _audit(cur, fmt_id, "update", actor, before, after)
        conn.commit()
    invalidate_cache()
    logger.info("SMS format %s updated by %s (enabled=%s)", fmt_id, actor, row["enabled"])
    return _public_format(after)


@router.delete("/{fmt_id}")
def delete_format(fmt_id: int, user: CurrentUser = Depends(require_sms_format_editor)):
    actor = _actor(user)
    with get_connection() as conn:
        cur = conn.cursor()
        before = _get_row(cur, fmt_id)
        cur.execute("DELETE FROM sms_formats WHERE id=%s AND country_code=%s", (fmt_id, COUNTRY.code))
        _audit(cur, fmt_id, "delete", actor, before, None)
        conn.commit()
    invalidate_cache()
    logger.info("SMS format %s deleted by %s", fmt_id, actor)
    return {"ok": True}


@router.get("/{fmt_id}/history")
def format_history(fmt_id: int, user: CurrentUser = Depends(require_sms_format_editor)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, action, actor, before, after, at FROM sms_format_audit "
            "WHERE format_id = %s ORDER BY at DESC LIMIT 100",
            (fmt_id,),
        )
        rows = _rows(cur)
    for r in rows:
        r["at"] = r["at"].isoformat() if r.get("at") else None
    return {"rows": rows}


def _formats_with_draft(kind: str, draft: Optional[dict], delete_id: Optional[int] = None) -> list[dict]:
    """Active formats of *kind* with *draft* inserted / replacing its saved version."""
    replace_id = draft.get("id") if draft else None
    out = [f for f in active_formats(kind) if f.get("id") not in (replace_id, delete_id)]
    if draft and draft["kind"] == kind:
        out.append(draft)
    out.sort(key=lambda f: (f.get("priority", 100), f.get("id") or 10**12))
    return out


def classify(content: str, sender: str, draft: Optional[dict] = None,
             delete_id: Optional[int] = None, use_draft: bool = True) -> dict:
    """What the pipeline would do with one SMS (no DB writes)."""
    d = draft if use_draft else None
    pay_formats = _formats_with_draft("payment", d, delete_id if use_draft else None)
    parsed = parse_payment(content, sender, _builtin_parser(), formats=pay_formats)
    if parsed:
        return {"kind": "payment", "parsed": _public(parsed)}
    bal_formats = _formats_with_draft("balance_request", d, delete_id if use_draft else None)
    hit = match_balance_request(content, sender, formats=bal_formats)
    if hit:
        return {"kind": "balance_request", "parsed": _public(hit)}
    return {"kind": "unparsed", "parsed": None}


@router.post("/test")
def test_format(body: TestIn, user: CurrentUser = Depends(require_sms_format_editor)):
    draft = _compile_or_400(_draft_row(body.draft, body.draft.id)) if body.draft else None
    draft_result = apply_format(draft, body.text, body.sender) if draft else None
    pipeline = classify(body.text, body.sender, draft)
    resolution = None
    with get_connection() as conn:
        settings = get_settings(conn)
        if pipeline["kind"] == "payment":
            from ingest import _resolve_gateway_account

            parsed = pipeline["parsed"]
            account, allocation, _remark, reason = _resolve_gateway_account(conn, body.text, parsed)
            resolution = {"account": account, "allocation": allocation, "reason": reason}
            conn.rollback()
    trusted = sender_is_trusted(settings, body.sender, pipeline.get("parsed"))
    return {
        "draft_result": _public(draft_result),
        "draft_matched": draft_result is not None,
        "pipeline": pipeline,
        "resolution": resolution,
        "sender_trusted": trusted,
        "sender_mode": settings["trusted_senders_mode"],
        "compiled_regex": draft["rx"].pattern if draft else None,
    }


def _recent_inbound(cur, days: int, limit: int) -> list[dict]:
    cur.execute(
        """SELECT id, received_at, sender, content, outcome, amount, account_number, receipt_key
             FROM sms_inbound_log
            WHERE received_at > NOW() - (%s || ' days')::interval
            ORDER BY received_at DESC LIMIT %s""",
        (str(days), limit),
    )
    return _rows(cur)


def _summary(c: dict) -> dict:
    p = c.get("parsed") or {}
    return {
        "kind": c["kind"],
        "amount": p.get("amount"),
        "account_hint": p.get("account_hint") or p.get("remark_raw") or "",
        "txn_id": p.get("txn_id") or "",
        "provider": p.get("provider") or "",
        "format_name": p.get("format_name") or "",
    }


@router.post("/preview")
def preview_change(body: PreviewIn, user: CurrentUser = Depends(require_sms_format_editor)):
    draft = _compile_or_400(_draft_row(body.draft, body.draft.id)) if body.draft else None
    with get_connection() as conn:
        rows = _recent_inbound(conn.cursor(), body.days, body.limit)
    counts = {"total": len(rows), "unchanged": 0, "newly_parsed": 0, "no_longer_parsed": 0, "changed": 0}
    examples = []
    for r in rows:
        before = _summary(classify(r["content"], r["sender"] or "", use_draft=False))
        after = _summary(classify(r["content"], r["sender"] or "", draft, body.delete_id))
        if before == after:
            counts["unchanged"] += 1
            continue
        if before["kind"] == "unparsed":
            change = "newly_parsed"
        elif after["kind"] == "unparsed":
            change = "no_longer_parsed"
        else:
            change = "changed"
        counts[change] += 1
        if len(examples) < 100:
            examples.append({
                "log_id": r["id"],
                "received_at": r["received_at"].isoformat() if r.get("received_at") else None,
                "sender": r["sender"],
                "content": r["content"][:400],
                "outcome": r["outcome"],
                "change": change,
                "before": before,
                "after": after,
            })
    return {"days": body.days, "counts": counts, "examples": examples}


def write_settings(body: SettingsIn, user: CurrentUser):
    updates: list[tuple[str, str]] = []
    if body.trusted_senders is not None:
        updates.append(("sms_trusted_senders", "\n".join(s.strip() for s in body.trusted_senders if s.strip())))
    if body.trusted_senders_mode is not None:
        if body.trusted_senders_mode not in SENDER_MODES:
            raise HTTPException(400, f"trusted_senders_mode must be one of {', '.join(SENDER_MODES)}")
        updates.append(("sms_trusted_senders_mode", body.trusted_senders_mode))
    if body.balance_replies_enabled is not None:
        updates.append(("sms_balance_replies_enabled", "1" if body.balance_replies_enabled else "0"))
    if body.balance_reply_template is not None:
        updates.append(("sms_balance_reply_template", body.balance_reply_template.strip()))
    if body.balance_unregistered_template is not None:
        updates.append(("sms_balance_unregistered_template", body.balance_unregistered_template.strip()))
    actor = _actor(user)
    with get_connection() as conn:
        before = get_settings(conn)
        if (body.trusted_senders_mode == "enforce"
                and not (body.trusted_senders if body.trusted_senders is not None else before["trusted_senders"])):
            raise HTTPException(400, "Add at least one trusted sender before enforcing the list.")
        cur = conn.cursor()
        for key, value in updates:
            cur.execute(
                "INSERT INTO system_config (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
        after = get_settings(conn)
        _audit(cur, None, "settings", actor, {"settings": before}, {"settings": after})
        conn.commit()
    logger.info("SMS format settings updated by %s: %s", actor, [k for k, _ in updates])
    return after


@router.get("/senders")
def observed_senders(days: int = Query(30, ge=1, le=180),
                     user: CurrentUser = Depends(require_sms_format_editor)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT sender, COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE parsed_ok) AS parsed,
                      MAX(received_at) AS last_seen
                 FROM sms_inbound_log
                WHERE received_at > NOW() - (%s || ' days')::interval
                GROUP BY sender ORDER BY COUNT(*) FILTER (WHERE parsed_ok) DESC, COUNT(*) DESC
                LIMIT 200""",
            (str(days),),
        )
        rows = _rows(cur)
        settings = get_settings(conn)
    for r in rows:
        r["last_seen"] = r["last_seen"].isoformat() if r.get("last_seen") else None
        r["trusted"] = sender_is_trusted(settings, r["sender"] or "")
    return {"days": days, "senders": rows}


_UNPROCESSED = ("unparsed", "no_account", "untrusted_sender")


def _possible_manual_duplicate(cur, account: str, amount: float, received_at) -> Optional[dict]:
    """A non-SMS payment of the same amount near the SMS time (e.g. entered via Record Payment)."""
    if not account or not amount or not received_at:
        return None
    cur.execute(
        """SELECT id, transaction_date, transaction_amount, source, payment_reference
             FROM transactions
            WHERE UPPER(account_number) = UPPER(%s)
              AND is_payment
              AND ABS(transaction_amount - %s) < 0.01
              AND transaction_date BETWEEN %s AND %s
              AND COALESCE(source, '') <> 'sms_gateway'
            ORDER BY transaction_date LIMIT 1""",
        (account, amount, received_at - timedelta(days=2), received_at + timedelta(days=14)),
    )
    rows = _rows(cur)
    if not rows:
        return None
    r = rows[0]
    r["transaction_date"] = r["transaction_date"].isoformat() if r.get("transaction_date") else None
    r["transaction_amount"] = float(r["transaction_amount"]) if r.get("transaction_amount") is not None else None
    return r


def _unprocessed_rows(cur, days: int, ids: Optional[list[int]] = None) -> list[dict]:
    if ids:
        cur.execute(
            """SELECT id, received_at, gateway_msg_id, sender, content, outcome, error
                 FROM sms_inbound_log WHERE id = ANY(%s) AND outcome = ANY(%s)""",
            (ids, list(_UNPROCESSED)),
        )
    else:
        cur.execute(
            """SELECT id, received_at, gateway_msg_id, sender, content, outcome, error
                 FROM sms_inbound_log
                WHERE outcome = ANY(%s) AND received_at > NOW() - (%s || ' days')::interval
                ORDER BY received_at DESC LIMIT 500""",
            (list(_UNPROCESSED), str(days)),
        )
    return _rows(cur)


def _evaluate_unprocessed(conn, row: dict) -> dict:
    from ingest import _resolve_gateway_account
    from mpesa_sms import mpesa_receipt_in_use

    c = classify(row["content"], row["sender"] or "", use_draft=False)
    out = {
        "log_id": row["id"],
        "received_at": row["received_at"].isoformat() if row.get("received_at") else None,
        "sender": row["sender"],
        "content": row["content"],
        "outcome": row["outcome"],
        "now": _summary(c),
        "account": None,
        "already_credited": False,
        "possible_manual_duplicate": None,
    }
    if c["kind"] == "payment":
        cur = conn.cursor()
        parsed = c["parsed"]
        receipt = (parsed.get("txn_id") or parsed.get("reference") or "").strip()
        out["already_credited"] = bool(receipt) and mpesa_receipt_in_use(conn, receipt)
        account, _alloc, _remark, _reason = _resolve_gateway_account(conn, row["content"], parsed)
        out["account"] = account
        if account:
            out["possible_manual_duplicate"] = _possible_manual_duplicate(
                cur, account, parsed["amount"], row.get("received_at"))
    return out


@router.get("/unprocessed")
def list_unprocessed(days: int = Query(30, ge=1, le=180),
                     user: CurrentUser = Depends(require_sms_format_editor)):
    with get_connection() as conn:
        rows = _unprocessed_rows(conn.cursor(), days)
        items = [_evaluate_unprocessed(conn, r) for r in rows]
        conn.rollback()
    return {"days": days, "rows": items}


@router.post("/replay")
def replay_unprocessed(body: ReplayIn, background_tasks: BackgroundTasks,
                       user: CurrentUser = Depends(require_sms_format_editor)):
    if COUNTRY.code == "BN" and not benin_replay_credit_enabled():
        return {
            "results": [
                {
                    "log_id": log_id,
                    "status": "skipped",
                    "reason": "Benin replay credit is disabled (SMS_BN_REPLAY_CREDIT_ENABLED)",
                }
                for log_id in body.log_ids
            ]
        }

    from ingest import _sms_incoming_process_raw

    actor = _actor(user)
    results = []
    with get_connection() as conn:
        rows = _unprocessed_rows(conn.cursor(), 180, body.log_ids)
        evaluated = [(_evaluate_unprocessed(conn, r), r) for r in rows]
        conn.rollback()
    found = {e["log_id"] for e, _ in evaluated}
    for missing in sorted(set(body.log_ids) - found):
        results.append({"log_id": missing, "status": "skipped", "reason": "not an unprocessed SMS"})
    for ev, row in evaluated:
        if ev["now"]["kind"] != "payment":
            results.append({"log_id": row["id"], "status": "skipped", "reason": "still not a payment"})
            continue
        if ev["already_credited"]:
            results.append({"log_id": row["id"], "status": "skipped", "reason": "already credited"})
            continue
        if ev["possible_manual_duplicate"] and not body.allow_possible_duplicates:
            results.append({"log_id": row["id"], "status": "skipped",
                            "reason": "possible manual duplicate", "match": ev["possible_manual_duplicate"]})
            continue
        replay_id = f"{row.get('gateway_msg_id') or 'log'}#replay{row['id']}"
        received = row.get("received_at") or datetime.now(timezone.utc)
        payload = {"messages": [{
            "id": replay_id,
            "from": row["sender"] or "",
            "content": row["content"],
            "sms_received": int(received.timestamp() * 1000),
        }]}
        _sms_incoming_process_raw(json.dumps(payload).encode(), background_tasks, contract_fee_gateway=False)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, outcome, transaction_id FROM sms_inbound_log "
                "WHERE gateway_msg_id = %s ORDER BY id DESC LIMIT 1",
                (replay_id,),
            )
            new = cur.fetchone()
            # A replay that errored leaves the original listed so it can be retried.
            finished = bool(new) and new[1] not in (None, "error", "resolving")
            if finished:
                cur.execute(
                    "UPDATE sms_inbound_log SET outcome='replayed', error=%s WHERE id=%s",
                    (f"replayed by {actor} as log {new[0]}", row["id"]),
                )
            conn.commit()
        logger.info("SMS log %s replayed by %s -> %s", row["id"], actor, new)
        results.append({"log_id": row["id"], "status": "replayed" if finished else "failed",
                        "reason": None if finished else f"replay outcome: {new[1] if new else 'none'}",
                        "new_log_id": new[0] if new else None,
                        "outcome": new[1] if new else None,
                        "transaction_id": new[2] if new else None})
    return {"results": results}
