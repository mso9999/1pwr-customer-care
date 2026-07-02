"""Flutter-shaped ``CountryConfig`` packs served by the mobile app BFF.

These packs are the remote equivalent of the bundled JSON assets in
``1PWRBENIN-v2/assets/config/country_*.json``. Serving them from CC lets
payment providers, feature flags, fees, and other amounts change without
an app build — the mobile app loads the pack from
``GET /api/app/country-config/{code}`` (URL advertised as ``appConfigUrl``
in ``/api/app/active-countries``) and falls back to its bundled asset
only when the remote pack is unreachable.

Two layers of data:

1. **App-facing metadata** (API base URL, payment providers, payment
   paths, meter-LAN hints, feature flags, quick recharge amounts, zones,
   onboarding / starting-kit fee defaults) — declared here as Python
   constants keyed by country code in :data:`_APP_PACKS`.
2. **Live, editable values** (connection / readyboard fees, tariff rate,
   onboarding / starting-kit fees) — read from ``system_config`` when a
   DB connection is supplied, falling back to the defaults declared here
   / in :mod:`country_config`. This is the same ``system_config`` table
   that ``/api/admin/country-fees`` writes, so finance / O&M edits land
   in the app within the endpoint's cache TTL.

The schema is a superset of ``1PWRBENIN-v2/assets/config/country_bn.json``;
new fields are optional so older app builds keep working. The contract is
documented in ``1PWR CC/docs/app-bff-contract.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from country_config import _REGISTRY

logger = logging.getLogger("cc-api.app-packs")


@dataclass(frozen=True)
class PaymentProviderSpec:
    """One selectable payment provider in the app's recharge/checkout UI."""

    id: str
    display_name: str
    icon_asset: Optional[str] = None
    # Value sent to the recharge API as the ``methode`` field. Defaults to
    # ``display_name`` when unset (matches the legacy app behaviour).
    api_method: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "displayName": self.display_name}
        if self.icon_asset:
            out["iconAsset"] = self.icon_asset
        if self.api_method:
            out["apiMethod"] = self.api_method
        return out


@dataclass(frozen=True)
class ZoneSpec:
    """A concession/site the app shows in auth + onboarding zone pickers.

    ``code`` is the short sigle appended to the client number (e.g. ``SAM``);
    ``name`` is the full village/site label used in onboarding search.
    """

    code: str
    name: str

    def to_json(self) -> Dict[str, Any]:
        return {"code": self.code, "name": self.name}


@dataclass(frozen=True)
class AppPackSpec:
    """Static, app-facing metadata for one country."""

    api_base_url: str
    request_timeout_seconds: int = 30
    locale_tag: str = "fr_FR"
    app_title: Optional[str] = None
    payment_providers: List[PaymentProviderSpec] = field(default_factory=list)
    payment_paths: Dict[str, str] = field(default_factory=dict)
    meter_lan: Optional[Dict[str, Any]] = None
    features: Dict[str, bool] = field(default_factory=dict)
    quick_recharge_amounts: List[float] = field(default_factory=list)
    zones: List[ZoneSpec] = field(default_factory=list)
    # Fee defaults (currency units). Live values come from system_config
    # when a DB connection is supplied; these are the seeds.
    onboarding_fee: float = 0.0
    starting_kit_fee: float = 0.0


# ---------------------------------------------------------------------------
# Per-country app pack metadata
# ---------------------------------------------------------------------------
#
# Keep these in sync with the bundled JSON assets in
# ``1PWRBENIN-v2/assets/config/country_*.json``. The remote pack is the
# source of truth once ``appConfigUrl`` is advertised; the bundled asset
# is the offline fallback.

_BENIN_PACK = AppPackSpec(
    api_base_url="https://app.onepowerbenin.com/api",
    request_timeout_seconds=30,
    locale_tag="fr_FR",
    app_title="1PWR",
    payment_providers=[
        PaymentProviderSpec(
            id="mtn_momo",
            display_name="MTN MoMo",
            icon_asset="assets/images/mtnlogo.png",
            api_method="MTN MoMo",
        ),
        PaymentProviderSpec(
            id="orange_money",
            display_name="Orange Money",
            icon_asset="assets/images/orangelogo.png",
            api_method="Orange Money",
        ),
    ],
    payment_paths={
        "momoPrefix": "momo",
        "initiate": "momo/initiate",
        "statusPrefix": "momo/status",
        "recharger": "recharger",
        "historyLastThree": "momo/history/last-three",
        "historyLastFive": "momo/history/last-five",
    },
    meter_lan={
        "softApSsidPrefixes": ["1PWR", "ONEMETER", "MESH"],
        "localApiBasePath": "/v1",
        "mdnsHost": "onemeter.local",
    },
    features={
        "momo": True,
        "meterLan": True,
        "messaging": True,
        "startingKit": True,
    },
    quick_recharge_amounts=[1000.0, 2000.0, 5000.0],
    zones=[
        ZoneSpec("GBO", "Gbowele"),
        ZoneSpec("SAM", "Samionta"),
        ZoneSpec("AGL", "Aglamidjodji"),
        ZoneSpec("KOT", "Koto"),
    ],
    onboarding_fee=10000.0,
    starting_kit_fee=40000.0,
)

_LESOTHO_PACK = AppPackSpec(
    api_base_url="https://cc.1pwrafrica.com/api",
    request_timeout_seconds=30,
    locale_tag="en_LS",
    app_title="1PWR Customer",
    payment_providers=[
        PaymentProviderSpec(
            id="mpesa",
            display_name="M-Pesa",
            icon_asset="assets/images/mpesalogo.png",
            api_method="M-Pesa",
        ),
        PaymentProviderSpec(
            id="ecocash",
            display_name="EcoCash",
            icon_asset="assets/images/ecocashlogo.png",
            api_method="EcoCash",
        ),
    ],
    payment_paths={
        "momoPrefix": "momo",
        "initiate": "momo/initiate",
        "statusPrefix": "momo/status",
        "recharger": "recharger",
        "historyLastThree": "momo/history/last-three",
        "historyLastFive": "momo/history/last-five",
    },
    meter_lan={
        "softApSsidPrefixes": ["1PWR", "ONEMETER"],
        "localApiBasePath": "/v1",
        "mdnsHost": "onemeter.local",
    },
    features={
        "momo": True,
        "meterLan": True,
        "messaging": True,
        "startingKit": False,
    },
    quick_recharge_amounts=[50.0, 100.0, 200.0],
    zones=[ZoneSpec(code, name) for code, name in sorted({
        "MAK": "Ha Makebe", "MAS": "Mashai", "SHG": "Sehonghong",
        "LEB": "Lebakeng", "SEH": "Sehlabathebe", "MAT": "Matsoaing",
        "TLH": "Tlhanyaku", "TOS": "Tosing", "SEB": "Sebapala",
        "RIB": "Ribaneng", "KET": "Ketane", "LSB": "Lets'eng-la-Baroa",
        "NKU": "Ha Nkau", "MET": "Methalaneng", "BOB": "Bobete",
        "MAN": "Manamaneng",
    }.items())],
    onboarding_fee=0.0,
    starting_kit_fee=0.0,
)

_APP_PACKS: Dict[str, AppPackSpec] = {
    "BN": _BENIN_PACK,
    "LS": _LESOTHO_PACK,
}


# ---------------------------------------------------------------------------
# Live system_config reads (graceful fallback when no DB supplied)
# ---------------------------------------------------------------------------

# Keys that may override AppPackSpec / CountryConfig defaults from system_config.
_ONBOARDING_FEE_KEY = "onboarding_fee_amount"
_STARTING_KIT_FEE_KEY = "starting_kit_fee_amount"
_CONNECTION_FEE_KEY = "connection_fee_amount"
_READYBOARD_FEE_KEY = "readyboard_fee_amount"
_TARIFF_RATE_KEY = "tariff_rate"


def _read_system_float(conn, key: str, fallback: float) -> float:
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s LIMIT 1", (key,))
    row = cur.fetchone()
    if not row:
        return fallback
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Pack builder
# ---------------------------------------------------------------------------

def supported_codes() -> List[str]:
    """Country codes that have an app pack ready to serve."""
    return sorted(_APP_PACKS.keys())


def build_pack(code: str, conn=None) -> Optional[Dict[str, Any]]:
    """Build the Flutter-shaped JSON pack for *code*.

    Returns ``None`` when no pack is registered for *code*.

    When *conn* is a live PostgreSQL connection, live ``system_config``
    values override the fee / tariff defaults so finance edits surface in
    the app without a redeploy. Without a connection the pack is built
    from the static defaults (used by tests and offline smoke runs).
    """
    code = code.upper()
    spec = _APP_PACKS.get(code)
    cfg = _REGISTRY.get(code)
    if spec is None or cfg is None:
        return None

    # Live overrides from system_config (country's own DB).
    if conn is not None:
        onboarding_fee = _read_system_float(conn, _ONBOARDING_FEE_KEY, spec.onboarding_fee)
        starting_kit_fee = _read_system_float(conn, _STARTING_KIT_FEE_KEY, spec.starting_kit_fee)
        connection_fee = _read_system_float(conn, _CONNECTION_FEE_KEY, cfg.default_connection_fee)
        readyboard_fee = _read_system_float(conn, _READYBOARD_FEE_KEY, cfg.default_readyboard_fee)
        tariff_rate = _read_system_float(conn, _TARIFF_RATE_KEY, cfg.default_tariff_rate)
    else:
        onboarding_fee = spec.onboarding_fee
        starting_kit_fee = spec.starting_kit_fee
        connection_fee = cfg.default_connection_fee
        readyboard_fee = cfg.default_readyboard_fee
        tariff_rate = cfg.default_tariff_rate

    # kWh divisor: the app converts balance -> kWh as balance / tariff_rate.
    # Keep ``kwhDivisor`` as the explicit field the app reads, derived from
    # the live tariff so tariff edits flow through without an app build.
    kwh_divisor = tariff_rate if tariff_rate else cfg.default_tariff_rate

    pack: Dict[str, Any] = {
        "countryCode": code,
        "displayName": cfg.display_name or cfg.name,
        "apiBaseUrl": spec.api_base_url,
        "ccApiBaseUrl": "https://cc.1pwrafrica.com/api",
        "requestTimeoutSeconds": spec.request_timeout_seconds,
        "localeTag": spec.locale_tag,
        "currencyCode": cfg.currency,
        "appTitle": spec.app_title,
        "features": dict(spec.features),
        "paymentProviders": [p.to_json() for p in spec.payment_providers],
        "paymentPaths": dict(spec.payment_paths),
        # New fields (all optional for legacy app builds):
        "kwhDivisor": kwh_divisor,
        "tariffRate": tariff_rate,
        "quickRechargeAmounts": list(spec.quick_recharge_amounts),
        "zones": [z.to_json() for z in spec.zones],
        "fees": {
            "onboardingFee": onboarding_fee,
            "startingKitFee": starting_kit_fee,
            "connectionFee": connection_fee,
            "readyboardFee": readyboard_fee,
            "currency": cfg.currency,
        },
    }
    if spec.meter_lan is not None:
        pack["meterLan"] = dict(spec.meter_lan)
    return pack
