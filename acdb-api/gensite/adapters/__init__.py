"""
Vendor adapter registry for gensite telemetry.

Each adapter implements `InverterAdapter` (see .base) and is registered in
REGISTRY below. The commission wizard offers `REGISTRY.keys()` as the vendor
dropdown; the poller dispatches on `site_credentials.vendor`.

Phase 1 status:
    victron   — implemented (Victron VRM public REST API)
    solarman  — stub       (Deye LSB / Deye SAM via Solarman OpenAPI)
    sinosoar  — stub       (Sinosoar Cloud — sinosoarcloud.com SPA + JSON XHR)
    sma       — stub       (SMA Sunny Portal — session scrape)
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
