"""
Plant Ops (gen) API — standalone service for the extracted gensite subsystem.

Phase 4 of the OM portal refactor: power-plant monitoring/control (the gensite
router + store + crypto, plus LPG) is its own service on gen.1pwrafrica.com, no
longer a sub-router of the Customer Care API. It reuses the CC codebase in
place — same venv, same Postgres, same JWT secret — but exposes ONLY:

  - the auth router      (/api/auth/* — employee-login, Nexus SSO, verify, me)
  - the gensite router   (plant sites, telemetry, inverter control)
  - the lpg router       (/api/lpg/* — LPG sites, batches, runs)
  - the portfolios router(/api/portfolios — country/portfolio picker)
  - /api/config          (country metadata the frontend expects)

Run as its own systemd unit (gen-api.service) on a separate port. Because it
shares JWT_SECRET with the CC API, tokens are interchangeable; because it shares
the database, no data migration is needed — this is a process/deployment
extraction, not a data split.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import router as auth_router
from gensite import router as gensite_router
from lpg import router as lpg_router
from pr_lookup import router as portfolio_router

app = FastAPI(
    title="1PWR Plant Ops (gen) API",
    description="Power-plant monitoring, control, and LPG — extracted from Customer Care.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(gensite_router)
app.include_router(lpg_router)          # /api/lpg/*
app.include_router(portfolio_router)    # /api/portfolios — country/portfolio picker


@app.get("/api/config")
def country_config_endpoint():
    """Country-specific metadata the frontend expects (same shape as CC)."""
    from country_config import COUNTRY
    return {
        "country_code": COUNTRY.code,
        "country_name": COUNTRY.name,
        "currency": COUNTRY.currency,
        "currency_symbol": COUNTRY.currency_symbol,
        "dial_code": COUNTRY.dial_code,
        "sites": COUNTRY.site_abbrev,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "gen-api"}
