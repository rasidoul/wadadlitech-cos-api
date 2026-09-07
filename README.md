# WadadliTech Chief of Staff API

Secure middleware between the WadadliTech Chief of Staff agent (`WD-AI-001`)
and WadadliTech business systems (HighLevel, Google, internal task registry,
TradeHub, and downstream AI agents).

GitHub repository access (repositories, commits, branches, issues, pull
requests, and files) is handled through a dedicated **direct GitHub
connector** outside this API, not through the WadadliTech COS API. The COS
API no longer aggregates GitHub engineering activity; the deprecated
`/github/...` routes now return `410 Gone`.

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

## Google integration (Gmail, Calendar, Drive)

[services/google.py](services/google.py) uses **OAuth 2.0 user authorization**
(not a service account or bare API key) with a long-lived refresh token,
requested with direct REST calls via `httpx` — the same lightweight pattern
used for the HighLevel integration (no `google-api-python-client`
dependency is required).

Authentication is already environment-variable based and makes no local-file
or `localhost`-callback assumptions at runtime, so it works unchanged on
Render:

```python
# services/google.py — token refresh via env vars, no local files
response = await client.post(
    "https://oauth2.googleapis.com/token",
    data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    },
)
```

### What was missing

- **Drive had no integration at all** — no routes, no service functions.
- Gmail only exposed a profile lookup (no message search/read).
- Calendar only exposed the calendar list (no event search).
- The refresh token was originally issued with only `gmail.readonly` and
  `calendar.readonly` scopes — **no Drive scope** — so Drive calls would
  fail even after adding code, until the token is re-issued.

### Scopes (least-privilege, read-only)

| Service | Scope |
|---|---|
| Gmail | `https://www.googleapis.com/auth/gmail.readonly` |
| Calendar | `https://www.googleapis.com/auth/calendar.readonly` |
| Drive | `https://www.googleapis.com/auth/drive.readonly` |

`scripts/test_google_connection.py` now requests all three scopes when
minting a refresh token.

### Endpoints

| Method | Path | operation_id | Purpose |
|---|---|---|---|
| GET | `/google/status` | `getGoogleStatus` | legacy combined Gmail+Calendar check |
| GET | `/google/gmail/messages` | `searchGmailMessages` | search mail (`query`, `limit`) |
| GET | `/google/gmail/messages/{message_id}` | `getGmailMessage` | read one message |
| GET | `/google/calendar/events` | `listCalendarEvents` | upcoming/searchable events |
| GET | `/google/drive/files` | `searchDriveFiles` | search/list accessible files |
| GET | `/google/drive/files/{file_id}` | `getDriveFileMetadata` | file metadata |
| GET | `/google/drive/files/{file_id}/content` | `getDriveFileContent` | best-effort text content (Docs/Sheets/Slides exported as text/CSV; other binary types are not supported for preview) |
| GET | `/integrations/google/status` | `getGoogleIntegrationStatus` | sanitized per-service configured/connected report |
| GET | `/integrations/google/gmail/test` | `testGoogleGmailConnection` | minimal read-only Gmail check |
| GET | `/integrations/google/calendar/test` | `testGoogleCalendarConnection` | minimal read-only Calendar check |
| GET | `/integrations/google/drive/test` | `testGoogleDriveConnection` | minimal read-only Drive check |

All of the above require the same `COS_API_KEY` Bearer token as every other
endpoint. None of them ever return `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`,
or access tokens — connection failures are reduced to a short sanitized code
(e.g. `invalid_grant`) via `_sanitize_error()`.

### Re-authorizing (required for Drive to work)

Because the current `GOOGLE_REFRESH_TOKEN` predates the Drive scope, it must
be reissued:

1. Revoke existing access at <https://myaccount.google.com/permissions>
   (optional but ensures a fresh consent).
2. Run `python scripts/test_google_connection.py` locally.
3. Update `GOOGLE_REFRESH_TOKEN` in both your local `.env` and in Render with
   the newly printed refresh token.

### Required Google Cloud APIs

Enable these APIs in the Google Cloud project tied to `GOOGLE_CLIENT_ID`
(Google Cloud Console → APIs & Services → Library):

- Gmail API
- Google Calendar API
- Google Drive API

### Startup diagnostics

On boot, the API logs (values never logged, only presence):

```
Google GOOGLE_CLIENT_ID configured: YES
Google GOOGLE_CLIENT_SECRET configured: YES
Google GOOGLE_REFRESH_TOKEN configured: YES
Google Gmail integration configured: YES
Google Calendar integration configured: YES
Google Drive integration configured: YES
```

If any variable is missing, a `WARNING: <VAR_NAME> is not configured. Google
services will be unavailable.` line is logged instead.

## TradeHub Integration

[services/tradehub.py](services/tradehub.py) is the trusted COS client for the
deployed TradeHub Chief of Staff API (`https://tradehub-7l1b.onrender.com`,
configurable via `TRADEHUB_BASE_URL`). It follows the same pattern as the
HighLevel client: a single module owns the base URL, Bearer auth,
timeouts, JSON decoding, and error handling — route handlers never call
`httpx` directly.

Every request sends `Authorization: Bearer <TRADEHUB_COS_API_KEY>`, which
must match TradeHub's own `COS_API_KEY`. The client raises `TradeHubError`
(with subclasses `TradeHubTimeoutError`, `TradeHubApprovalError`) for missing
configuration, network errors, HTTP errors, invalid JSON, and TradeHub's
`success: false` envelope — never leaking the API key or raw stack traces.

### Endpoints

| Method | Path | operation_id | Purpose |
|---|---|---|---|
| GET | `/tradehub/status` | `getTradeHubStatus` | app/database/runtime health |
| GET | `/tradehub/accounts` | `getTradeHubAccounts` | demo/live account summaries |
| GET | `/tradehub/open-trades` | `getTradeHubOpenTrades` | open trades (`account_type`, `instrument`, `broker`) |
| GET | `/tradehub/trades` | `getTradeHubTradeHistory` | trade history (`period`, `instrument`, `account_type`, `broker`, `limit`) |
| GET | `/tradehub/performance` | `getTradeHubPerformance` | daily/weekly/monthly/all performance (authoritative — never recalculated here) |
| GET | `/tradehub/runtime` | `getTradeHubRuntime` | desired vs. actual auto-trading worker state |
| GET | `/tradehub/actions` | `getTradeHubActions` | COS-originated action history (`period`, `action_type`, `execution_status`, `limit`) |
| POST | `/tradehub/trade-request` | `requestTradeHubTrade` | **financial execution** — requires `approval_confirmed: true` |
| POST | `/tradehub/close-request` | `requestTradeHubClose` | **financial execution** — requires `approval_confirmed: true` |
| GET | `/integrations/tradehub/status` | `getTradeHubIntegrationStatus` | sanitized configured/reachable/authenticated/healthy report |

All require the standard `COS_API_KEY` Bearer token, same as every other COS
endpoint.

### Read-only vs. financial execution

WD-AI-001 may autonomously call every `GET` endpoint above (`READ_ONLY`).
`requestTradeHubTrade` and `requestTradeHubClose` are `FINANCIAL_EXECUTION`
and always require `approval_confirmed: true` in the request body — this is
never inferred from the presence of a user message.

### Demo vs. live safety gates

`COS_TRADEHUB_LIVE_EXECUTION_ENABLED` (default `false`) is an independent
COS-side gate. A request with `account_type: "live"` is rejected unless
both `approval_confirmed: true` **and** `COS_TRADEHUB_LIVE_EXECUTION_ENABLED=true`
are set. TradeHub separately enforces its own `ALLOW_LIVE_TRADING` /
`COS_LIVE_TRADE_EXECUTION_ENABLED` — this is defense in depth, not a
replacement.

### Request IDs and reconciliation

`request_tradehub_trade()` / `request_tradehub_close()` generate
`wd-cos-trade-<uuid>` / `wd-cos-close-<uuid>` request IDs (or accept a
caller-supplied one, e.g. for a reconciliation retry). On an ambiguous
timeout (`TradeHubTimeoutError`), the original `request_id` is preserved on
the exception — the COS API never mints a replacement ID and resends, since
the broker action may have already occurred. Call
`reconcile_action_by_request_id(request_id)` (backed by
`GET /tradehub/actions`) to determine the actual outcome before deciding
what to report or do next.

### Source of truth

For trading data, TradeHub's live API is authoritative. This client never
recalculates performance — engineering activity must never be used to infer
trading performance.

### Executive summary integration

The `trading` section of `GET /executive-summary` calls
`get_tradehub_executive_summary()`, which never raises — a TradeHub outage
degrades that one section instead of failing the whole briefing. It reports
service health, runtime (desired vs. actual), today's realized/unrealized
P/L, and open trade count, and only elevates alerts for significant issues
(e.g. auto-trader configured but not running, or TradeHub unavailable).
Routine profitable/loss activity does not generate an alert.
`source_health.tradehub` (bool) and `source_health_status.tradehub`
(`HEALTHY`/`DEGRADED`/`UNAVAILABLE`) are also exposed alongside the existing
CRM health fields. The `engineering` section of `/executive-summary` is a
static placeholder (`"source_of_truth": "direct_github_connector"`) since
GitHub engineering data is no longer aggregated by the COS API.

> Field names read from TradeHub's runtime/performance responses use
> defensive fallbacks (multiple possible key names) since this client was
> built without access to the TradeHub repository. Verify field names
> against TradeHub's actual response shape once connectivity is confirmed
> in Render, and adjust `get_tradehub_executive_summary()` if needed.

### Verifying connectivity

`scripts/test_tradehub_connection.py` is an opt-in script (never run
automatically) that calls the real `GET /api/cos/status` endpoint using the
configured environment variables and never prints the Bearer token:

```bash
python scripts/test_tradehub_connection.py
```

## Environment variables

See [.env.example](.env.example) for the full list. New for this release:

```
MAYA_WD_WEBHOOK_URL=
TRADEHUB_BASE_URL=https://tradehub-7l1b.onrender.com
TRADEHUB_COS_API_KEY=
TRADEHUB_TIMEOUT_SECONDS=15
COS_TRADEHUB_LIVE_EXECUTION_ENABLED=false
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
4. After changing `GOOGLE_REFRESH_TOKEN` (e.g. re-authorizing for Drive
   access), update it in Render and restart the service — Render restarts
   do not persist locally-generated files, so the token must come from the
   environment variable, never a local `token.json`/`credentials.json`.

## Render Environment Variables

Enter these in **Render → wadadlitech-cos-api → Environment**. Values are
never committed to the repository.

**COS**
```
COS_API_KEY
```

**HighLevel**
```
HIGHLEVEL_WADADLITECH_TOKEN
HIGHLEVEL_WADADLITECH_LOCATION_ID
HIGHLEVEL_PARADIGM_TOKEN
HIGHLEVEL_PARADIGM_LOCATION_ID
HIGHLEVEL_JERMAINGORDON_TOKEN
HIGHLEVEL_JERMAINGORDON_LOCATION_ID
```

**Google**
```
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
GOOGLE_REFRESH_TOKEN
```

**Agent briefs**
```
MAYA_WD_WEBHOOK_URL
```

**TradeHub** — `TRADEHUB_COS_API_KEY` must match the shared secret set as
`COS_API_KEY` on the TradeHub Render service. Keep
`COS_TRADEHUB_LIVE_EXECUTION_ENABLED` set to `false` until live execution is
a deliberate, reviewed decision.
```
TRADEHUB_BASE_URL
TRADEHUB_COS_API_KEY
TRADEHUB_TIMEOUT_SECONDS
COS_TRADEHUB_LIVE_EXECUTION_ENABLED
```

