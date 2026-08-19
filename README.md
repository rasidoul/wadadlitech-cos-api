# WadadliTech Chief of Staff API

Secure middleware between the WadadliTech Chief of Staff agent (`WD-AI-001`)
and WadadliTech business systems (HighLevel, GitHub, Google, internal task
registry, and downstream AI agents).

- Base URL: `https://api.wadadlitech.com`
- Current version: `2.2.0`

## Authentication

All protected endpoints require a Bearer token that matches the server's
`COS_API_KEY` environment variable.

```
Authorization: Bearer <COS_API_KEY>
```

| Condition | Status |
|---|---|
| Missing `Authorization` header | `401` |
| Invalid/incorrect bearer token | `403` |
| Server has no `COS_API_KEY` configured | `500` |

## Agent registry

Agents the Chief of Staff can address are defined in
[config/agents.py](config/agents.py):

| agent_id | name | department | reports_to |
|---|---|---|---|
| `WD-AI-001` | Chief of Staff | Executive | — |
| `MAYA-WD-MKT-001` | Maya | Marketing | `WD-AI-001` |

Endpoints:

- `GET /agents` — list registered agents.
- `GET /agents/{agent_id}` — fetch one agent (`404` if unknown).
- `GET /agents/{agent_id}/status` — registration, webhook, and queue health.

### Maya's authority

Maya may autonomously research, monitor trends, draft content, and recommend
campaigns/service offerings/content calendars/competitive intelligence. Maya
must **not** autonomously publish public content, launch paid ads, spend
money, change prices, make contractual commitments, or publish unverified
claims — those require approval from Jermain Gordon. The `requires_human_approval`
field on a brief lets Maya (or an integration) distinguish research
assignments from approval-controlled actions.

## Brief lifecycle

Briefs are created with `POST /agents/{agent_id}/briefs` and persist in a
dedicated SQLite database managed by
[services/agent_briefs.py](services/agent_briefs.py)
(`data/agent_briefs.db`), following the same storage pattern used for tasks
(`services/tasks.py` / `data/cos_tasks.db`).

Lifecycle (`status` field):

```
QUEUED -> DELIVERED -> ACKNOWLEDGED -> IN_PROGRESS -> COMPLETED
```

`DELIVERY_FAILED` is reachable from `QUEUED`/`DELIVERED` and can be retried
back into `QUEUED`/`DELIVERED`. Invalid transitions return `409`.

`delivery_status` tracks the outbound HighLevel webhook attempt separately
from the brief's business lifecycle: `PENDING`, `PENDING_CONFIGURATION`,
`DELIVERED`, or `FAILED`. A brief is always stored **before** any delivery
attempt, so delivery failures never lose the brief.

### Endpoints

| Method | Path | operation_id |
|---|---|---|
| POST | `/agents/{agent_id}/briefs` | `createAgentBrief` |
| GET | `/agents/{agent_id}/briefs` | `listAgentBriefs` |
| GET | `/agents/{agent_id}/briefs/{brief_id}` | `getAgentBrief` |
| PATCH | `/agents/{agent_id}/briefs/{brief_id}` | `updateAgentBrief` |
| POST | `/agents/{agent_id}/briefs/{brief_id}/acknowledge` | `acknowledgeAgentBrief` |
| POST | `/agents/{agent_id}/briefs/{brief_id}/complete` | `completeAgentBrief` |
| POST | `/agents/{agent_id}/briefs/{brief_id}/retry-delivery` | `retryAgentBriefDelivery` |

`GET /agents/{agent_id}/briefs` supports `status`, `brief_type`, `priority`,
`limit`, and `offset` query filters.

### Idempotency

Retried Monday-automation submissions will not create duplicate briefs.
Provide either:

- `Idempotency-Key: <unique-value>` request header, or
- `"external_reference": "<unique-value>"` in the request body.

A repeated submission with the same key/reference for the same agent returns
the existing brief record instead of creating a new one.

## HighLevel webhook delivery

Maya is associated with the **WadadliTech** HighLevel account (not Paradigm).
The existing HighLevel Agent Studio `GET` endpoints only retrieve agents —
they cannot push a message/assignment to an agent. Delivery to Maya is done
by POSTing the structured brief to an admin-configured inbound webhook URL,
stored only in the `MAYA_WD_WEBHOOK_URL` environment variable. The API never
accepts a delivery URL from the request body (this would allow SSRF), and the
webhook URL is never included in API responses.

- If `MAYA_WD_WEBHOOK_URL` is unset: the brief is stored, `delivery_status`
  is `PENDING_CONFIGURATION`, and the create request still returns `201`.
- If configured and the webhook responds with a 2xx status: `delivery_status`
  becomes `DELIVERED`, `delivered_at` is set.
- If configured but the request fails or returns a non-2xx status:
  `delivery_status` becomes `FAILED` with a safe (non-secret) error message,
  and the brief's `status` becomes `DELIVERY_FAILED`. Retry with
  `POST /agents/{agent_id}/briefs/{brief_id}/retry-delivery`.

> Maya has not actually received a brief inside HighLevel until
> `MAYA_WD_WEBHOOK_URL` is configured with a real HighLevel inbound webhook
> and a live end-to-end delivery has been verified.

## Environment variables

See [.env.example](.env.example) for the full list. New for this release:

```
MAYA_WD_WEBHOOK_URL=
```

## Local testing

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
python -m pytest tests/ -v
uvicorn main:app --reload
```

Then visit `http://127.0.0.1:8000/docs` or `http://127.0.0.1:8000/openapi.json`.

Automated tests mock all outbound HTTP calls (HighLevel webhook, etc.) and use
an isolated SQLite database — they never touch production data or the live
HighLevel account.

## Production deployment

1. Set all environment variables from `.env.example` on the host (never
   commit `.env`).
2. Ensure the `data/` directory is writable and persisted across deploys —
   `cos_tasks.db` and `agent_briefs.db` live there.
3. To enable Maya's webhook delivery in production, set
   `MAYA_WD_WEBHOOK_URL` to the WadadliTech HighLevel inbound webhook URL and
   redeploy/restart. Confirm with a real brief creation followed by a check
   in HighLevel before considering delivery "live".
