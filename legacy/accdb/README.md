# Legacy ACCDB Scripts

These files are preserved for historical reference only.

## Status

- They belong to the deprecated ACCDB / Windows era of the CC system.
- They are not part of the active `1PDB`-backed production architecture.
- They should not be treated as current operational runbooks.
- They live outside `acdb-api/` so the normal backend deploy does not ship them.

## Archived Files

- `import_meter_readings.py` — ACCDB aggregation and remote import pipeline
- `compact_accdb.py` — Access compact/repair helper
- `sync_accdb.ps1` — Windows ACCDB clone/sync script
- `snapshot.py` — ACCDB snapshot retention helper
- `setup.bat` — Windows API setup helper
- `install-service.bat` — Windows service installer helper

## When To Use

Only read these files when doing:

- historical forensics
- migration provenance work
- legacy decommissioning

If you need the live runtime or data truth, use `1PDB` and the active CC backend instead.
