"""
gensite — generation-site commissioning, credential store, and inverter telemetry.

Public surface:
    router              FastAPI router (registered in customer_api.py)
    adapters.REGISTRY   vendor → InverterAdapter map

Storage:
    1PDB tables: sites, site_equipment, site_credentials,
                 inverter_readings, inverter_alarms
    (migration acdb-api/migrations/013_gensite_equipment.sql)

Credentials at rest are Fernet-encrypted; key is read from
env var CC_CREDENTIAL_ENCRYPTION_KEY by gensite.crypto.
"""

from .router import router

__all__ = ["router"]
