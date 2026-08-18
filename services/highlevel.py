import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://services.leadconnectorhq.com"


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
    pass


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
            )
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
            )
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

async def search_contacts(
    account_key: str = "wadadlitech",
    limit: int = 20,
    query: Optional[str] = None,
    cursor: Optional[str] = None,
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

    account = _get_account(account_key)

    location = await get_location(
        account_key
    )

    pipelines = await get_pipelines(
        account_key
    )

    workflows = await get_workflows(
        account_key
    )

    contacts = await search_contacts(
        account_key,
        20,
        None,
    )

    opportunities = (
        await search_opportunities(
            account_key,
            100,
            None,
            None,
        )
    )

    agents = await get_agent_studio_agents(
        account_key,
        100,
        0,
        False,
    )

    location_data = location.get(
        "location",
        location,
    )

    contact_list = contacts.get(
        "contacts",
        [],
    )

    pipeline_list = pipelines.get(
        "pipelines",
        [],
    )

    workflow_list = workflows.get(
        "workflows",
        [],
    )

    opportunity_list = (
        opportunities.get(
            "opportunities",
            [],
        )
    )

    agent_list = (
        agents.get("agents")
        or agents.get("data")
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
                contacts.get("total")
                or contacts.get("count")
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
    }