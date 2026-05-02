"""
Monthly staff-PIN broadcast.

The CC employee login uses a date-based **staff PIN** (``date_password_for``
in :mod:`auth`) as a defense-in-depth gate on top of HR-portal validation.
The PIN rotates at 00:00 UTC on the 1st of every month, so without a
broadcast the whole team gets locked out for the few hours it takes word
to spread (RCA: 2026-05-01).

This module composes the announcement and pushes it to the country's
WhatsApp Customer Care bridge using the verbatim ``/broadcast`` route on
the bridge (added 2026-05-01). Trigger paths:

* **Scheduled**: ``scripts/ops/broadcast_monthly_pin.py`` invoked by the
  systemd timer ``cc-auth-pin-broadcast.timer`` at ~04:00 UTC on the 1st.
* **Manual**: ``POST /api/admin/auth/broadcast-pin`` (superadmin only).

Both paths funnel through :func:`broadcast_pin_for_country`.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional

from auth import date_password_for
from cc_bridge_notify import broadcast_to_bridge

logger = logging.getLogger("cc-api.auth-pin-broadcast")


def _month_label(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def compose_pin_message(
    year: int,
    month: int,
    *,
    include_next_month: bool = True,
    include_login_url: bool = True,
) -> str:
    """Render the broadcast text for a given month.

    Pure function -- no I/O. Kept verbatim-renderable so the bridge sends
    it as-is.
    """
    pin = date_password_for(year, month)
    lines: List[str] = [
        f"🔐 1PWR Customer Care — Staff PIN for {_month_label(year, month)}",
        "",
        f"This month's CC employee login PIN is:  *{pin}*",
        "",
        "Use your employee ID + this PIN to log in.",
    ]
    if include_login_url:
        lines.append("Portal: https://cc.1pwrafrica.com")
    if include_next_month:
        # Compute the following month for advance notice.
        if month == 12:
            ny, nm = year + 1, 1
        else:
            ny, nm = year, month + 1
        next_pin = date_password_for(ny, nm)
        lines.extend([
            "",
            f"Heads-up: from the 1st of {_month_label(ny, nm)} the PIN will rotate to *{next_pin}*.",
        ])
    lines.extend([
        "",
        "The PIN rotates automatically at 00:00 UTC on the 1st of every month -- "
        "this message is sent shortly after the rotation so the team isn't locked out.",
    ])
    return "\n".join(lines)


def broadcast_pin_for_country(
    country_code: str,
    *,
    when: Optional[datetime] = None,
    include_next_month: bool = True,
    jid: Optional[str] = None,
) -> dict:
    """Compose and push the PIN broadcast for a single country.

    Returns a result dict suitable for HTTP / log inspection. Never raises:
    bridge failures are logged + reported in the result.
    """
    when = when or datetime.now(timezone.utc)
    msg = compose_pin_message(
        when.year, when.month, include_next_month=include_next_month,
    )
    ok = broadcast_to_bridge(msg, country_code=country_code, jid=jid)
    pin = date_password_for(when.year, when.month)
    result = {
        "country_code": country_code.upper(),
        "year": when.year,
        "month": when.month,
        "month_label": _month_label(when.year, when.month),
        "pin_prefix": pin[:1] + "***",  # don't echo full PIN in API/log responses
        "ok": ok,
    }
    if ok:
        logger.info(
            "PIN broadcast sent country=%s month=%s",
            country_code, result["month_label"],
        )
    else:
        logger.warning(
            "PIN broadcast FAILED country=%s month=%s -- check CC_BRIDGE_NOTIFY_URL[_%s] and bridge state",
            country_code, result["month_label"], country_code.upper(),
        )
    return result


def broadcast_pin_for_active_countries(
    *,
    when: Optional[datetime] = None,
    include_next_month: bool = True,
    only: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Broadcast to every active country (or a subset filtered by ``only``).

    Each country is attempted independently -- one bridge being down doesn't
    block the others. Inactive countries (``CountryConfig.active=False``,
    e.g. the Zambia placeholder) are skipped.
    """
    from country_config import _REGISTRY  # type: ignore[attr-defined]

    only_set = {c.upper() for c in only} if only else None
    out: List[dict] = []
    for code, cfg in _REGISTRY.items():
        if not cfg.active:
            continue
        if only_set and code.upper() not in only_set:
            continue
        out.append(broadcast_pin_for_country(
            code, when=when, include_next_month=include_next_month,
        ))
    return out


def is_first_week_of_month(when: Optional[datetime] = None) -> bool:
    """Return True if *when* (default: now-UTC) is in the first 7 days of a month.

    Used by :mod:`auth` to surface a friendlier 401 message to staff who
    are likely getting bitten by the PIN rotation.
    """
    when = when or datetime.now(timezone.utc)
    return when.day <= 7
