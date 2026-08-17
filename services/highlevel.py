import os
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

HIGHLEVEL_TOKEN = os.getenv("HIGHLEVEL_TOKEN")
HIGHLEVEL_LOCATION_ID = os.getenv("HIGHLEVEL_LOCATION_ID")

BASE_URL = "https://services.leadconnectorhq.com"


class HighLevelError(Exception):
    pass


def _require_config():
    if not HIGHLEVEL_TOKEN:
        raise HighLevelError("HIGHLEVEL_TOKEN is not configured.")

    if not HIGHLEVEL_LOCATION_ID:
        raise HighLevelError("HIGHLEVEL_LOCATION_ID is not configured.")


def _headers(version: str = "2021-07-28") -> Dict[str, str]:
    _require_config()

    return {
        "Authorization": f"Bearer {HIGHLEVEL_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Version": version,
    }


async def _get(
    path: str,
    version: str = "2021-07-28",
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}{path}",
            headers=_headers(version),
            params=params,
        )

    if response.status_code >= 400:
        raise HighLevelError(
            f"HighLevel returned {response.status_code}: {response.text}"
        )

    return response.json()


async def _post(
    path: str,
    version: str = "2021-07-28",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{BASE_URL}{path}",
            headers=_headers(version),
            json=payload or {},
        )

    if response.status_code >= 400:
        raise HighLevelError(
            f"HighLevel returned {response.status_code}: {response.text}"
        )

    return response.json()


async def get_location() -> Dict[str, Any]:
    return await _get(
        f"/locations/{HIGHLEVEL_LOCATION_ID}",
        version="2021-07-28",
    )


async def get_pipelines() -> Dict[str, Any]:
    return await _get(
        "/opportunities/pipelines",
        version="2021-07-28",
        params={
            "locationId": HIGHLEVEL_LOCATION_ID,
        },
    )


async def get_workflows() -> Dict[str, Any]:
    return await _get(
        "/workflows/",
        version="2021-07-28",
        params={
            "locationId": HIGHLEVEL_LOCATION_ID,
        },
    )


async def get_tags() -> Dict[str, Any]:
    return await _get(
        f"/locations/{HIGHLEVEL_LOCATION_ID}/tags",
        version="2021-07-28",
    )


async def get_conversations(limit: int = 20) -> Dict[str, Any]:
    return await _get(
        "/conversations/search",
        version="2021-04-15",
        params={
            "locationId": HIGHLEVEL_LOCATION_ID,
            "limit": limit,
        },
    )


async def search_contacts(
    limit: int = 20,
    query: Optional[str] = None,
) -> Dict[str, Any]:

    payload: Dict[str, Any] = {
        "locationId": HIGHLEVEL_LOCATION_ID,
        "pageLimit": limit,
        "page": 1,
    }

    if query:
        payload["query"] = query

    return await _post(
        "/contacts/search",
        version="2021-07-28",
        payload=payload,
    )


async def search_opportunities(
    limit: int = 20,
    query: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:

    payload: Dict[str, Any] = {
        "locationId": HIGHLEVEL_LOCATION_ID,
        "pageLimit": limit,
        "page": 1,
    }

    if query:
        payload["query"] = query

    if status:
        payload["status"] = status

    return await _post(
        "/opportunities/search",
        version="2021-07-28",
        payload=payload,
    )


async def get_agent_studio_agents(
    limit: int = 50,
    offset: int = 0,
    published_only: bool = False,
) -> Dict[str, Any]:

    params = {
        "locationId": HIGHLEVEL_LOCATION_ID,
        "limit": limit,
        "offset": offset,
    }

    if published_only:
        params["isPublished"] = "true"

    return await _get(
        "/agent-studio/agent",
        version="2021-07-28",
        params=params,
    )