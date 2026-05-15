# uGridPlan ↔ Customer Care sync contract

This note ties the **uGridPlan** adapter (`onepowerLS/uGridPlan`, path `web/adapter/main.py` in the UGP repo) to **Customer Care** (`acdb-api/sync_ugridplan.py`). It exists so engineers changing either side know which identifiers mean what.

## uGridPlan side (authoritative behaviour)

### Project registry vs session

1. **Published projects** live in a JSON **registry** on the UGP host (`_load_project_registry()`). Each entry is keyed by a **registry key** (often composite, e.g. `TOS_minigrid`, `MAK_minigrid`). The `/projects` list returns summaries including `name`, `code`, and portfolio metadata.

2. **`POST /projects/{project_name}/load`** (`load_project_version` in `main.py` ~21381):
   - Resolves the path parameter through **`_resolve_project_name`** (~17612): accepts either the **full registry key** or a **bare `code`** field that matches a single published project.
   - Materialises the latest (or requested) version into memory and registers a **session-scoped `projectId`** (UUID). This UUID is **not** stable across loads; it is only valid for that authenticated session.

3. **`GET /project/table-data`** (~23583): reads rows for `elementType=connection` (and others) using **`projectId`** (the session UUID), not the registry key.

4. **Pushing updates from CC** uses **`POST /project/batch-connection-update`** (~32376): body includes `projectId` (session UUID) and `updates` keyed by **Survey_ID** (with fallbacks documented in UGP for `connection_<index>` rows).

### Auth

UGP can require JWT (`/auth/login`). CC uses **`UGP_SERVICE_USER`** / password (or equivalent) in `sync_ugridplan.py` — keep service credentials aligned with UGP’s auth policy.

## Customer Care side (`acdb-api/sync_ugridplan.py`)

### Client (`UGPClient`)

- **`UGP_BASE_URL`**: defaults to `https://dev.ugp.1pwrafrica.com/api`; production should point at `https://ugp.1pwrafrica.com/api` when syncing live data.
- **`load_project(project_name)`**: mirrors UGP resolution — try literal key, then `{name}_minigrid` / `_ci` / `_ipp`, then resolve via **`GET /projects`** display name → code, then composite keys again. Failures surface as `RuntimeError` with the message CC wraps as `uGridPLAN fetch failed: …`.
- **`get_connections(session_id)`**: calls **`GET /project/table-data`** with `elementType=connection` and pagination.

### Site ↔ registry mapping (`cc_site_projects` in SQLite auth DB)

- **`site_code`**: CC concession code (e.g. `TOS`, `MAK`) — same notion as `community` / account suffix in 1PDB.
- **`project_id` column**: stores the **uGrid registry key string** passed to `/projects/{key}/load` (see comment in `sync_ugridplan.py` ~1373). It must be a key UGP accepts, not the session UUID.

**Operational rule:** After **Discover** or manual edits, verify mappings in the CC UI (or `GET /api/sync/sites`) against UGP’s **Project Browser** / registry key (often `SITE_minigrid`). A wrong string here produces load failures or the wrong network.

### Discover heuristics (fixed 2026-05)

Auto-discover previously used naive substring matching; e.g. uGrid project code **`sin`** was incorrectly associated with site **TOS** because **`sin`** appears inside **`tosing`**. CC now uses token-boundary rules in `_ugp_discover_match_site`. Re-run **Discover** or correct rows if legacy bad mappings exist.

### Resilience

`GET /api/sync/connections` may retry loading with **`site_code`** as the registry key when the stored `project_id` fails (`RuntimeError`), and returns **`ugp_registry_key`** so operators can see which key actually loaded.

## Reference paths

| Concern | uGridPlan repo (`uGridPlan map_v3/`) | CC repo |
|--------|--------------------------------------|----------|
| Load + session | `web/adapter/main.py` — `load_project_version`, `_resolve_project_name` | `acdb-api/sync_ugridplan.py` — `UGPClient.load_project` |
| Connection table | `get_table_data` | `UGPClient.get_connections` |
| Batch push | `batch_connection_update` | CC sync execute paths in same module |
| CC-only mapping | — | `cc_site_projects`, `discover_projects`, `list_connections` |

## Related

- `CONTEXT.md` — Related System: uGridPlan (portal URLs, no shared code).
- `SESSION_LOG.md` — batch-connection-update / Survey_ID indexing notes when debugging CC → UGP pushes.
