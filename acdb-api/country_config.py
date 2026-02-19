"""
Country configuration module.

Reads COUNTRY_CODE from the environment and exports site maps, currency,
dial code, Koios org ID, and other country-specific constants.

Supported countries:
  LS  — Lesotho  (OnePower Lesotho, LSL, M-PESA)
  BN  — Benin    (MIONWA GENERATION, XOF, MTN MoMo)
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class CountryConfig:
    code: str                           # ISO 3166-1 alpha-2
    name: str
    currency: str                       # ISO 4217
    currency_symbol: str                # display prefix (M, CFA, etc.)
    dial_code: str                      # international dialing prefix
    koios_org_id: str
    timezone: str                       # IANA timezone (e.g. Africa/Maseru)
    utc_offset_hours: int               # fixed offset for simple arithmetic
    default_tariff_rate: float          # default currency/kWh for balance engine
    site_abbrev: Dict[str, str]         # site_code → full name
    site_districts: Dict[str, str]      # site_code → district/region
    koios_sites: Dict[str, str]         # site_code → Koios UUID
    payment_regex_id: str               # which M-PESA/MoMo regex set to use


LESOTHO = CountryConfig(
    code="LS",
    name="Lesotho",
    currency="LSL",
    currency_symbol="M",
    dial_code="266",
    koios_org_id="1cddcb07-6647-40aa-aaaa-70d762922029",
    timezone="Africa/Maseru",
    utc_offset_hours=2,
    default_tariff_rate=5.0,
    site_abbrev={
        "MAK": "Ha Makebe",
        "MAS": "Mashai",
        "SHG": "Sehonghong",
        "LEB": "Lebakeng",
        "SEH": "Sehlabathebe",
        "MAT": "Matsoaing",
        "TLH": "Tlhanyaku",
        "TOS": "Tosing",
        "SEB": "Sebapala",
        "RIB": "Ribaneng",
        "KET": "Ketane",
        "LSB": "Lets'eng-la-Baroa",
        "NKU": "Ha Nkau",
        "MET": "Methalaneng",
        "BOB": "Bobete",
        "MAN": "Manamaneng",
    },
    site_districts={
        "MAK": "Maseru", "MAS": "Thaba-Tseka", "SHG": "Thaba-Tseka",
        "LEB": "Qacha's Nek", "SEH": "Qacha's Nek", "MAT": "Mokhotlong",
        "TLH": "Mokhotlong", "TOS": "Quthing", "SEB": "Quthing",
        "RIB": "Mafeteng", "KET": "Mohale's Hoek",
        "NKU": "Maseru", "MET": "Thaba-Tseka", "BOB": "Thaba-Tseka",
        "MAN": "Thaba-Tseka",
    },
    koios_sites={
        "KET": "a075cbc1-e920-455e-9d5a-8595061dfec0",
        "LSB": "ed0766c4-9270-4254-a107-eb4464a96ed9",
        "MAS": "101c443e-6500-4a4d-8cdc-6bd15f4388c8",
        "MAT": "2f7c38b8-4a70-44fd-bf9c-ebf2b2aa78c0",
        "SEH": "0a4fdca5-2d78-4979-8051-10f21a216b16",
        "SHG": "bd7c477d-0742-4056-b75c-38b14ac7cf97",
        "TLH": "db5bf699-31ea-44b6-91c5-1b41e4a2d130",
        "RIB": "10f0846e-d541-4340-81d1-e667cb5026ba",
        "TOS": "b564c8d6-a6c1-43d4-98d1-87ed8cd8ffd7",
    },
    payment_regex_id="mpesa_ls",
)

BENIN = CountryConfig(
    code="BN",
    name="Benin",
    currency="XOF",
    currency_symbol="CFA",
    dial_code="229",
    koios_org_id="0123589c-7f1f-4eb4-8888-d8f8aa706ea4",
    timezone="Africa/Porto-Novo",
    utc_offset_hours=1,
    default_tariff_rate=160.0,
    site_abbrev={
        "GBO": "Gbo",
        "SAM": "Sam",
    },
    site_districts={
        "GBO": "Zou",
        "SAM": "Zou",
    },
    koios_sites={
        "GBO": "a23c334e-33f7-473d-9ae3-9e631d5336e4",
        "SAM": "8f80b0a8-0502-4e26-9043-7152979360aa",
    },
    payment_regex_id="momo_bj",
)

_REGISTRY: Dict[str, CountryConfig] = {
    "LS": LESOTHO,
    "BN": BENIN,
}


def get_country(code: Optional[str] = None) -> CountryConfig:
    """Return the active country config.

    Reads COUNTRY_CODE env var if *code* is not passed explicitly.
    Defaults to 'LS' (Lesotho) for backward compatibility.
    """
    code = (code or os.environ.get("COUNTRY_CODE", "LS")).upper()
    cfg = _REGISTRY.get(code)
    if cfg is None:
        raise ValueError(f"Unknown COUNTRY_CODE '{code}'. Valid: {sorted(_REGISTRY)}")
    return cfg


COUNTRY: CountryConfig = get_country()
SITE_ABBREV: Dict[str, str] = COUNTRY.site_abbrev
KNOWN_SITES: Set[str] = set(SITE_ABBREV.keys())
SITE_DISTRICTS: Dict[str, str] = COUNTRY.site_districts
KOIOS_SITES: Dict[str, str] = COUNTRY.koios_sites
CURRENCY: str = COUNTRY.currency
CURRENCY_SYMBOL: str = COUNTRY.currency_symbol
TIMEZONE: str = COUNTRY.timezone
UTC_OFFSET_HOURS: int = COUNTRY.utc_offset_hours

_SITE_TO_COUNTRY: Dict[str, str] = {}
for _cc, _cfg in _REGISTRY.items():
    for _site in _cfg.site_abbrev:
        _SITE_TO_COUNTRY[_site] = _cc
_SITE_TO_COUNTRY["MAK"] = "LS"


def get_tariff_rate_for_site(site_code: str) -> float:
    """Return the tariff rate (currency/kWh) for a given site code."""
    cc = _SITE_TO_COUNTRY.get(site_code)
    if cc:
        return _REGISTRY[cc].default_tariff_rate
    return COUNTRY.default_tariff_rate


def get_currency_for_site(site_code: str) -> str:
    """Return the ISO 4217 currency code for a given site code."""
    cc = _SITE_TO_COUNTRY.get(site_code)
    if cc:
        return _REGISTRY[cc].currency
    return COUNTRY.currency
