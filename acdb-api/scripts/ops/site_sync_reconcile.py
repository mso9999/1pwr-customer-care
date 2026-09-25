#!/usr/bin/env python3
"""Nightly catch-up: pull the PR / Nexus master site list into this CC lane.

PR's ``fanoutSiteChanges`` pushes every site write to CC; this run repairs
anything a failed or skipped push left behind. The lane comes from the
``COUNTRY_CODE`` in the EnvironmentFile (one ExecStart per lane).
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    from country_config import COUNTRY
    from site_sync_ingest import reconcile_lane

    result = reconcile_lane("system:cc-site-sync-reconcile")
    staged = sorted(a["code"] for a in result["applied"] if a.get("action") == "staged")
    print(
        f"{COUNTRY.code}: catalog={result['catalog']} applied={result['applied_count']} "
        f"ignored={result['ignored_count']} newly_staged={staged or 'none'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
