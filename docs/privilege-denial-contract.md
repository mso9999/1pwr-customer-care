# Customer Care privilege-denial contract

Customer Care role gates must explain a refusal; a bare `403 Access denied` is not sufficient for an employee privilege decision.

## What the user sees

The route-level denial screen shows:

1. every effective CC role composed from the user's primary and secondary HR assignments;
2. the role or roles accepted for the requested action;
3. who can change each source of access; and
4. the sign-out/sign-in step needed to refresh the signed claim after a correction.

English and French text is supplied by the shared `common` locale.

## Backend response

`middleware.raise_privilege_denied()` returns HTTP 403 with a structured `detail` object:

```json
{
  "code": "privilege_denied",
  "system": "cc",
  "action": "assign a meter to a customer",
  "assigned_roles": ["engineering"],
  "required_roles": ["onm_team", "superadmin"],
  "message": "Your assigned CC role(s) are engineering. To assign a meter to a customer, you need one of: onm_team, superadmin.",
  "role_crud_owners": [
    {"owner": "Your organization’s country HR team", "manages": "Primary/secondary department assignments, Lead status, and assignment scope in HR."},
    {"owner": "Nexus/IS&T User Administrator", "manages": "Explicit Customer Care access or denial in Nexus, within access policy."},
    {"owner": "Customer Care Superadmin", "manages": "Manual CC roles and protected local CC actions."}
  ],
  "resolution": "Ask the appropriate owner to correct the assignment, then sign out and back in so Customer Care receives a fresh privilege claim."
}
```

The frontend API client converts this contract into a readable explanation for action-time errors. New protected endpoints must use `require_role(..., action="...")` or `raise_privilege_denied(...)` so they inherit the same contract.

## Role ownership

- Country HR owns the authoritative department memberships. Secondary assignments carry the same mapped privileges as primary assignments.
- Nexus/IS&T owns explicit Customer Care app enable/deny decisions.
- Customer Care Superadmins own manual CC role overrides and protected local operations.

An operator should not be sent indiscriminately to “IT.” The displayed owner identifies which layer contains the discrepancy.
