# OM Ticket API Contract (CC/UGP/OM)

This contract defines how Customer Care (`cc.1pwrafrica.com`) and other clients
integrate with `om.1pwrafrica.com` as the single source of truth for tickets.

## Canonical identity

- Canonical ticket reference: `ticket_id` (string) from OM.
- Compatibility keys accepted in responses during migration:
  - `ticket_id`
  - `ugp_ticket_id`
  - `id`
- CC stores/uses the canonical reference as `ugp_ticket_id` in compatibility
  contexts to avoid frontend breakage while local `wa_tickets` ownership is
  retired.

## Service-to-service trust model

Preferred model is service API key + forwarded user context headers:

- `X-API-Key: <OM_TICKETS_API_KEY>` (or `Authorization: Bearer <token>`)
- `X-OM-Source: cc` (or source-specific, e.g. `cc-gensite`)
- `X-CC-User-Id: <employee id>`
- `X-CC-User-Role: <cc role>`
- `X-CC-User-Name: <display name>`

This keeps OM authorization/auditing user-aware without exposing frontend CORS
or cross-domain authentication complexity.

## Endpoint contract

CC proxy endpoints:

- `GET /api/om-tickets`
  - query: `limit`, `offset`, `site_code`, `account_number`, `status`, `search`
  - response: `{ tickets: Ticket[], total: number, count: number }`
- `GET /api/om-tickets/{ticket_ref}`
  - response: `Ticket`
- `POST /api/om-tickets`
  - body: `Partial<Ticket>` for create
  - response: OM create payload (must include a canonical reference)
- `PATCH /api/om-tickets/{ticket_ref}`
  - body: partial update payload
  - response: OM update payload
- `POST /api/om-tickets/{ticket_ref}/comments`
  - body: `{ user?: string, text: string }`
  - response: OM comment payload
- `GET /api/om-tickets/export`
  - query: `site_code`, `status`, `quarter`
  - response: streamed Excel from OM backend

## Ticket field mapping (minimum)

- `site_code` -> OM site identifier
- `account_number` -> customer account reference
- `fault_description` -> problem narrative
- `ticket_name` -> short title
- `category`, `priority`, `status`
- `failure_time`, `restoration_time`, `duration`
- `reported_by`, `resolved_by`
- `services_affected`, `troubleshooting_steps`, `cause_of_fault`,
  `precautions`, `resolution_approach`
- `source` -> origin channel (`portal`, `whatsapp`, `gensite`, etc.)

## Error contract

CC proxy normalizes upstream errors as:

```json
{
  "message": "OM ticket API request failed",
  "upstream_status": 502,
  "upstream_detail": {}
}
```

For unavailable OM backend, CC returns `503` with upstream host + exception
context. Retry policy is limited to transient failures (`5xx` and network errors).
