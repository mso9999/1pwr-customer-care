"""
Vendor adapter registry for gensite telemetry.

QUARANTINED NON-POLICY AREA (for event-parity scope):
- Gensite adapter work is intentionally separate from current CC<->SM parity strategy.
- Do not include these adapter changes in parity PRs unintentionally.
- See docs/ops/non-policy-quarantine-registry.md.

Each adapter implements `InverterAdapter` (see .base) and is registered in
REGISTRY below. The commission wizard offers `REGISTRY.keys()` as the vendor
dropdown; the poller dispatches on `site_credentials.vendor`.

Status:
    victron   — ready  (Victron VRM REST API)
    deye      — ready  (Deye ESS Cloud OpenAPI — developer-ess-eu1.deyecloud.com)
    alphaess  — ready  (AlphaESS Cloud — sgcloud.alphaess.com/api)
    sinosoar  — ready  (Sinosoar Cloud — JeecgBoot SPA + CAPTCHA OCR)
    sma       — ready  (SMA Sunny Portal — Keycloak token + uiapi energybalance)
"""

from typing import Dict

from .base import (
    AdapterError,
    AlarmEvent,
    CredentialSpec,
    InverterAdapter,
    LiveReading,
    SiteCredential,
    SiteEquipment,
    VendorDescriptor,
    VerifyResult,
)
from .alphaess import AlphaESSAdapter
from .victron import VictronAdapter
from .solarman import SolarmanAdapter
from .sinosoar import SinosoarAdapter
from .sma import SMAAdapter

REGISTRY: Dict[str, InverterAdapter] = {
    "victron":  VictronAdapter(),
    "deye":     SolarmanAdapter(),     # Deye → Solarman OpenAPI
    "solarman": SolarmanAdapter(),     # same adapter, accessible under both names
    "sinosoar": SinosoarAdapter(),
    "sma":      SMAAdapter(),
    "alphaess": AlphaESSAdapter(),
}

__all__ = [
    "REGISTRY",
    "AdapterError",
    "AlarmEvent",
    "CredentialSpec",
    "InverterAdapter",
    "LiveReading",
    "SiteCredential",
    "SiteEquipment",
    "VendorDescriptor",
    "VerifyResult",
]
