"""
Adapter contract shared by all vendor telemetry backends.

Design: keep the protocol narrow so that REST clients, session scrapers,
and Modbus bridges can all satisfy the same interface. The poller and
router never need to know which kind they got.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Protocol


class AdapterError(RuntimeError):
    """Raised for adapter-level failures (auth, parse, transport).

    The `retryable` flag lets the poller decide whether to back off
    or mark the credential as broken.
    """

    def __init__(self, message: str, *, retryable: bool = True, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


# ---------------------------------------------------------------------------
# Value objects (pure data — no ORM coupling)
# ---------------------------------------------------------------------------

@dataclass
class CredentialSpec:
    """Describes the credential fields a vendor requires.

    Drives the commission wizard form and validation. `secret_fields` are
    Fernet-encrypted at rest; `plain_fields` are stored as-is (username,
    site ID, base URL override). `extra_fields` end up in the JSONB `extra`
    column for adapter-specific knobs (e.g. Solarman appid).
    """
    vendor: str
    backend: str
    label: str
    secret_fields: List[str] = field(default_factory=list)
    plain_fields: List[str] = field(default_factory=list)
    extra_fields: List[str] = field(default_factory=list)
    docs_url: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class VendorDescriptor:
    """What the frontend needs to render a vendor card."""
    vendor: str
    display_name: str
    backends: List[CredentialSpec]
    implementation_status: str  # 'ready' | 'stub' | 'scrape' | 'modbus'


@dataclass
class SiteCredential:
    """In-memory credential bundle handed to adapters. Plaintext — never logged."""
    site_code: str
    vendor: str
    backend: str
    base_url: Optional[str]
    username: Optional[str]
    secret: Optional[str]           # decrypted password/portal secret
    api_key: Optional[str]          # decrypted API key / app secret
    site_id_on_vendor: Optional[str]
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SiteEquipment:
    """Minimal equipment info adapters need for per-device dispatch."""
    id: int
    site_code: str
    vendor: str
    kind: str
    model: Optional[str]
    serial: Optional[str]
    role: Optional[str]


@dataclass
class VerifyResult:
    ok: bool
    message: str
    discovered_site_id: Optional[str] = None
    discovered_equipment: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class LiveReading:
    """One snapshot — the poller writes this into inverter_readings."""
    equipment_id: int
    ts_utc: datetime
    ac_kw: Optional[float] = None
    ac_kwh_total: Optional[float] = None
    dc_kw: Optional[float] = None
    pv_kw: Optional[float] = None
    battery_kw: Optional[float] = None
    battery_soc_pct: Optional[float] = None
    grid_kw: Optional[float] = None
    ac_freq_hz: Optional[float] = None
    ac_v_avg: Optional[float] = None
    status_code: Optional[str] = None
    raw_json: Optional[Dict[str, Any]] = None


@dataclass
class IntervalReading(LiveReading):
    """Same shape as LiveReading; distinguished by being backfill, not instantaneous."""
    pass


@dataclass
class AlarmEvent:
    equipment_id: Optional[int]
    site_code: str
    vendor_code: Optional[str]
    vendor_msg: Optional[str]
    severity: str                    # 'info' | 'warning' | 'critical'
    raised_at: datetime
    cleared_at: Optional[datetime] = None
    event_json: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class InverterAdapter(Protocol):
    """Every vendor adapter implements this shape."""

    vendor: str
    display_name: str
    implementation_status: str          # 'ready' | 'stub' | 'scrape' | 'modbus'

    def credential_specs(self) -> List[CredentialSpec]:
        """Return the credential field schema for the commission wizard."""
        ...

    def verify(self, cred: SiteCredential) -> VerifyResult:
        """Validate the credential by making a minimal authenticated request.

        Must not raise for auth failures — return VerifyResult(ok=False, ...).
        Raises AdapterError only for unexpected transport / logic failures.
        """
        ...

    def fetch_live(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
    ) -> List[LiveReading]:
        """Return the freshest snapshot for each piece of equipment.

        May return fewer rows than inputs if the vendor doesn't expose
        per-device telemetry; the poller tolerates that.
        """
        ...

    def fetch_day(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        day: date,
    ) -> List[IntervalReading]:
        """Return 15-min / hourly interval readings for the day (UTC).

        Used for backfill; may be a no-op for scrape adapters.
        """
        ...

    def fetch_alarms(
        self,
        cred: SiteCredential,
        equipment: List[SiteEquipment],
        since: datetime,
    ) -> List[AlarmEvent]:
        """Return new alarm events since `since`. May return []."""
        ...
