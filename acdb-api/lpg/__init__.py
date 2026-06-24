"""LPG (generator fuel) inventory + generator-run tracking.

Backs the operations flow for tracking LPG consumption, balance and cost per
site (flowchart 2026-06-23). See migrations/045_lpg_tracking.sql for the schema
and router.py for the HTTP surface (prefix /api/lpg).
"""

from .router import router  # noqa: F401
