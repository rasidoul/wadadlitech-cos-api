import json
import logging
import os
import uuid

from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wadadlitech.highlevel")

BASE_URL = "https://services.leadconnectorhq.com"

# 4xx validation failures are deterministic and must never be retried.
# 429/5xx/timeouts are transient and safe to retry (no retry loop exists
# yet in this service - this classification is exposed for callers/future use).
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}



# =========================================================
# HIGHLEVEL ACCOUNT REGISTRY
# =========================================================

HIGHLEVEL_ACCOUNTS = {
    "wadadlitech": {
        "name": "WadadliTech",
        "relationship": "internal",
        "token": os.getenv("HIGHLEVEL_WADADLITECH_TOKEN"),
        "location_id": os.getenv("HIGHLEVEL_WADADLITECH_LOCATION_ID"),
    },

    "paradigm": {
        "name": "Paradigm Homecare LLC",
        "relationship": "client",
        "token": os.getenv("HIGHLEVEL_PARADIGM_TOKEN"),
        "location_id": os.getenv("HIGHLEVEL_PARADIGM_LOCATION_ID"),
    },

    "jermaingordon": {
        "name": "JermainGordon",
        "relationship": "client",
        "token": os.getenv("HIGHLEVEL_JERMAINGORDON_TOKEN"),
        "location_id": os.getenv(
            "HIGHLEVEL_JERMAINGORDON_LOCATION_ID"
        ),
    },
}


class HighLevelError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


def _is_retryable(status_code: Optional[int]) -> bool:
    return status_code in RETRYABLE_STATUS_CODES


def _classify_error_type(status_code: Optional[int]) -> str:

    if status_code is None:
        return "CONNECTION_ERROR"

    if status_code in (401, 403):
        return "AUTH_ERROR"

    if status_code == 404:
        return "NOT_FOUND"

    if status_code == 422:
        return "UPSTREAM_VALIDATION_ERROR"

    if status_code == 429:
        return "RATE_LIMITED"

    if status_code >= 500:
        return "UPSTREAM_SERVER_ERROR"

    return "UPSTREAM_ERROR"


def _safe_upstream_message(response_text: Optional[str]) -> str:
    """Extract a short, non-sensitive message from a HighLevel error body.

    Never returns the raw response body verbatim, to avoid leaking CRM
    payload data or PII that may appear alongside validation errors.
    """

    if not response_text:
        return "HighLevel request failed."

    try:
        body = json.loads(response_text)
    except (TypeError, ValueError):
        return "HighLevel returned an unrecognized error response."

    message = body.get("message") if isinstance(body, dict) else None

    if isinstance(message, list):
        return "; ".join(str(item) for item in message[:5])

    if isinstance(message, str):
        return message

    error = body.get("error") if isinstance(body, dict) else None

    if isinstance(error, str):
        return error

    return "HighLevel rejected the request."


def normalize_highlevel_error(
    account_key: str,
    resource: str,
    exc: HighLevelError,
) -> Dict[str, Any]:
    """Build a sanitized, structured error record for a failed HighLevel resource call."""

    trace_id = uuid.uuid4().hex

    logger.warning(
        "HighLevel request failed account=%s resource=%s status=%s trace_id=%s",
        account_key,
        resource,
        exc.status_code,
        trace_id,
    )

    return {
        "provider": "highlevel",
        "account_key": account_key,
        "resource": resource,
        "http_status": exc.status_code,
        "error_type": _classify_error_type(exc.status_code),
        "message": _safe_upstream_message(exc.response_text),
        "trace_id": trace_id,
        "retryable": _is_retryable(exc.status_code),
    }


# =========================================================
# ACCOUNT HELPERS
# =========================================================

def get_registered_accounts() -> Dict[str, Any]:

    accounts = []

    for account_key, account in HIGHLEVEL_ACCOUNTS.items():

        accounts.append(
            {
                "account_key": account_key,
                "name": account["name"],
                "relationship": account["relationship"],
                "location_id": account["location_id"],
                "configured": bool(
                    account["token"]
                    and account["location_id"]
                ),
            }
        )

    return {
        "accounts": accounts,
        "count": len(accounts),
    }


def _get_account(
    account_key: str,
) -> Dict[str, Any]:

    normalized_key = account_key.lower()

    account = HIGHLEVEL_ACCOUNTS.get(
        normalized_key
    )

    if not account:
        raise HighLevelError(
            "Unknown HighLevel account '{}'. "
            "Valid accounts are: {}".format(
                account_key,
                ", ".join(
                    HIGHLEVEL_ACCOUNTS.keys()
                ),
            )
        )

    if not account.get("token"):
        raise HighLevelError(
            "HighLevel token is not configured "
            "for account '{}'.".format(
                normalized_key
            )
        )

    if not account.get("location_id"):
        raise HighLevelError(
            "HighLevel Location ID is not "
            "configured for account '{}'.".format(
                normalized_key
            )
        )

    return account


# =========================================================
# REQUEST HEADERS
# =========================================================

def _headers(
    account_key: str,
    version: str = "2021-07-28",
) -> Dict[str, str]:

    account = _get_account(account_key)

    return {
        "Authorization": "Bearer {}".format(
            account["token"]
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


# =========================================================
# GENERIC REQUEST FUNCTIONS
# =========================================================

async def _get(
    account_key: str,
    path: str,
    version: str = "2021-07-28",
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.get(
            "{}{}".format(
                BASE_URL,
                path,
            ),
            headers=_headers(
                account_key,
                version,
            ),
            params=params,
        )

    if response.status_code >= 400:

        raise HighLevelError(
            "HighLevel account '{}' returned "
            "{}: {}".format(
                account_key,
                response.status_code,
                response.text,
            ),
            status_code=response.status_code,
            response_text=response.text,
        )

    return response.json()


async def _post(
    account_key: str,
    path: str,
    version: str = "2021-07-28",
    payload: Optional[
        Dict[str, Any]
    ] = None,
) -> Dict[str, Any]:

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.post(
            "{}{}".format(
                BASE_URL,
                path,
            ),
            headers=_headers(
                account_key,
                version,
            ),
            json=payload or {},
        )

    if response.status_code >= 400:

        raise HighLevelError(
            "HighLevel account '{}' returned "
            "{}: {}".format(
                account_key,
                response.status_code,
                response.text,
            ),
            status_code=response.status_code,
            response_text=response.text,
        )

    return response.json()


# =========================================================
# LOCATION
# =========================================================

async def get_location(
    account_key: str = "wadadlitech",
) -> Dict[str, Any]:

    account = _get_account(account_key)

    return await _get(
        account_key,
        "/locations/{}".format(
            account["location_id"]
        ),
        version="2021-07-28",
    )


# =========================================================
# PIPELINES
# =========================================================

async def get_pipelines(
    account_key: str = "wadadlitech",
) -> Dict[str, Any]:

    account = _get_account(account_key)

    return await _get(
        account_key,
        "/opportunities/pipelines",
        version="2021-07-28",
        params={
            "locationId": account[
                "location_id"
            ]
        },
    )


# =========================================================
# WORKFLOWS
# =========================================================

async def get_workflows(
    account_key: str = "wadadlitech",
) -> Dict[str, Any]:

    account = _get_account(account_key)

    return await _get(
        account_key,
        "/workflows/",
        version="2021-07-28",
        params={
            "locationId": account[
                "location_id"
            ]
        },
    )


# =========================================================
# TAGS
# =========================================================

async def get_tags(
    account_key: str = "wadadlitech",
) -> Dict[str, Any]:

    account = _get_account(account_key)

    return await _get(
        account_key,
        "/locations/{}/tags".format(
            account["location_id"]
        ),
        version="2021-07-28",
    )


# =========================================================
# CONVERSATIONS
# =========================================================

async def get_conversations(
    account_key: str = "wadadlitech",
    limit: int = 20,
) -> Dict[str, Any]:

    account = _get_account(account_key)

    return await _get(
        account_key,
        "/conversations/search",
        version="2021-04-15",
        params={
            "locationId": account[
                "location_id"
            ],
            "limit": limit,
        },
    )


# =========================================================
# CONTACTS
# =========================================================

def _validate_page_limit(
    limit: Any,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    """Validate a COS-facing 'limit' before translating it to a provider field.

    HighLevel's /contacts/search endpoint rejects non-numeric or
    out-of-range pageLimit values with a 422 validation error.
    """

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise HighLevelError(
            "Invalid pagination limit: expected an integer.",
            status_code=422,
        )

    if limit < minimum or limit > maximum:
        raise HighLevelError(
            "Invalid pagination limit: must be between {} and {}.".format(
                minimum, maximum
            ),
            status_code=422,
        )

    return limit


async def search_contacts(
    account_key: str = "wadadlitech",
    limit: int = 20,
    query: Optional[str] = None,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:

    account = _get_account(account_key)

    page_limit = _validate_page_limit(limit)

    # HighLevel's /contacts/search endpoint requires "pageLimit"; it does
    # not accept "limit" and rejects it as an unsupported property.
    payload = {
        "locationId": account[
            "location_id"
        ],
        "pageLimit": page_limit,
    }

    if query:
        payload["query"] = query

    # v3 contacts search paginates with a cursor, not a page number
    if cursor:
        payload["nextCursor"] = cursor

    return await _post(
        account_key,
        "/contacts/search",
        version="2021-07-28",
        payload=payload,
    )


# =========================================================
# OPPORTUNITIES
# =========================================================

async def search_opportunities(
    account_key: str = "wadadlitech",
    limit: int = 20,
    query: Optional[str] = None,
    status: Optional[str] = None,
    start_after: Optional[int] = None,
    start_after_id: Optional[str] = None,
) -> Dict[str, Any]:

    account = _get_account(account_key)

    payload = {
        "locationId": account[
            "location_id"
        ],
        "limit": limit,
    }

    if query:
        payload["query"] = query

    if status:
        payload["status"] = status

    # opportunities search paginates with startAfter/startAfterId, not a page number
    if start_after is not None:
        payload["startAfter"] = start_after

    if start_after_id:
        payload["startAfterId"] = start_after_id

    return await _post(
        account_key,
        "/opportunities/search",
        version="2021-07-28",
        payload=payload,
    )


# =========================================================
# AGENT STUDIO
# =========================================================

async def get_agent_studio_agents(
    account_key: str = "wadadlitech",
    limit: int = 50,
    offset: int = 0,
    published_only: bool = False,
) -> Dict[str, Any]:

    account = _get_account(account_key)

    params = {
        "locationId": account[
            "location_id"
        ],
        "limit": limit,
        "offset": offset,
    }

    if published_only:
        params["isPublished"] = "true"

    return await _get(
        account_key,
        "/agent-studio/agent",
        version="2021-07-28",
        params=params,
    )


# =========================================================
# SINGLE-ACCOUNT EXECUTIVE SUMMARY
# =========================================================

async def get_account_summary(
    account_key: str,
) -> Dict[str, Any]:
    """Build a single-account summary.

    Account/location resolution failures are fundamental (bad auth or
    misconfiguration) and propagate as HighLevelError - callers treat
    that as UNAVAILABLE. Failures fetching individual resources
    (contacts, opportunities, pipelines, workflows, agents) are isolated
    so one bad resource degrades - rather than removes - the summary.
    """

    account = _get_account(account_key)

    # Fundamental connectivity check: if this fails, the account is
    # UNAVAILABLE and the error propagates to the caller unchanged.
    location = await get_location(
        account_key
    )

    resource_status: Dict[str, str] = {}
    resource_errors: Dict[str, Any] = {}

    async def _fetch(resource_name, call):
        try:
            result = await call
            resource_status[resource_name] = "HEALTHY"
            return result

        except HighLevelError as exc:
            resource_status[resource_name] = "ERROR"
            resource_errors[resource_name] = normalize_highlevel_error(
                account_key,
                resource_name,
                exc,
            )
            return None

    pipelines = await _fetch(
        "pipelines",
        get_pipelines(account_key),
    )

    workflows = await _fetch(
        "workflows",
        get_workflows(account_key),
    )

    contacts = await _fetch(
        "contacts",
        search_contacts(
            account_key,
            20,
            None,
        ),
    )

    opportunities = await _fetch(
        "opportunities",
        search_opportunities(
            account_key,
            100,
            None,
            None,
        ),
    )

    agents = await _fetch(
        "agents",
        get_agent_studio_agents(
            account_key,
            100,
            0,
            False,
        ),
    )

    location_data = location.get(
        "location",
        location,
    )

    contact_list = (contacts or {}).get(
        "contacts",
        [],
    )

    pipeline_list = (pipelines or {}).get(
        "pipelines",
        [],
    )

    workflow_list = (workflows or {}).get(
        "workflows",
        [],
    )

    opportunity_list = (
        (opportunities or {}).get(
            "opportunities",
            [],
        )
    )

    agent_list = (
        (agents or {}).get("agents")
        or (agents or {}).get("data")
        or []
    )

    open_opportunities = []
    won_opportunities = []
    lost_opportunities = []

    for opportunity in opportunity_list:

        status = str(
            opportunity.get(
                "status",
                "",
            )
        ).lower()

        if status == "won":

            won_opportunities.append(
                opportunity
            )

        elif status in (
            "lost",
            "abandoned",
        ):

            lost_opportunities.append(
                opportunity
            )

        else:

            open_opportunities.append(
                opportunity
            )

    open_value = sum(
        float(
            opportunity.get(
                "monetaryValue"
            )
            or 0
        )
        for opportunity
        in open_opportunities
    )

    overall_status = (
        "DEGRADED" if resource_errors else "HEALTHY"
    )

    return {
        "account_key": account_key,

        "name": account["name"],

        "relationship": account[
            "relationship"
        ],

        "location": {
            "id": location_data.get(
                "id"
            ),
            "name": location_data.get(
                "name"
            ),
            "timezone": location_data.get(
                "timezone"
            ),
        },

        "contacts": {
            "returned": len(
                contact_list
            ),
            "reported_total": (
                (contacts or {}).get("total")
                or (contacts or {}).get("count")
                or len(contact_list)
            ),
        },

        "pipelines": {
            "count": len(
                pipeline_list
            ),
            "items": [
                {
                    "id": item.get(
                        "id"
                    ),
                    "name": item.get(
                        "name"
                    ),
                }
                for item
                in pipeline_list
            ],
        },

        "opportunities": {
            "returned": len(
                opportunity_list
            ),
            "open_count": len(
                open_opportunities
            ),
            "won_count": len(
                won_opportunities
            ),
            "lost_count": len(
                lost_opportunities
            ),
            "open_value": open_value,
        },

        "workflows": {
            "count": len(
                workflow_list
            ),
            "items": [
                {
                    "id": item.get(
                        "id"
                    ),
                    "name": item.get(
                        "name"
                    ),
                    "status": item.get(
                        "status"
                    ),
                }
                for item
                in workflow_list
            ],
        },

        "agents": {
            "count": len(
                agent_list
            ),
            "items": [
                {
                    "id": item.get(
                        "id"
                    ),
                    "name": item.get(
                        "name"
                    ),
                    "status": item.get(
                        "status"
                    ),
                }
                for item
                in agent_list
            ],
        },

        # Overall status: HEALTHY when every resource succeeded, DEGRADED
        # when the account/location resolved but one or more resource
        # calls failed. UNAVAILABLE is signaled by this function raising
        # HighLevelError before reaching this point (see get_location above).
        "status": overall_status,

        "resources": resource_status,

        "errors": resource_errors or None,
    }