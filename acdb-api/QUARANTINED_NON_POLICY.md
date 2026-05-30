# `acdb-api` Quarantined Non-Policy Code

These files are currently outside the core event-parity merge scope and should
not be pulled into parity PRs unintentionally.

## Quarantined files

- `sparkmeter_customer.py` (customer-provisioning resilience stream)
- `gensite/adapters/__init__.py`
- `gensite/adapters/alphaess.py`
- `gensite/adapters/sinosoar.py`
- `gensite/adapters/solarman.py`

## In-scope parity file in `acdb-api`

- `crud.py` (CC -> SM credit path uses durable retry queue)

For promotion criteria and guardrails, see:
- `docs/ops/non-policy-quarantine-registry.md`

