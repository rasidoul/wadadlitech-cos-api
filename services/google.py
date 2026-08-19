import os
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


class GoogleError(Exception):
    pass


def _require_config():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured."
        )

    if not GOOGLE_REFRESH_TOKEN:
        raise GoogleError("GOOGLE_REFRESH_TOKEN is not configured.")


async def _get_access_token() -> str:

    _require_config()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
        )

    if response.status_code >= 400:
        raise GoogleError(
            "Google token refresh failed {}: {}".format(
                response.status_code,
                response.text,
            )
        )

    return response.json()["access_token"]


async def _get(base_url: str, path: str) -> Any:

    access_token = await _get_access_token()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "{}{}".format(base_url, path),
            headers={
                "Authorization": "Bearer {}".format(access_token)
            },
        )

    if response.status_code >= 400:
        raise GoogleError(
            "Google returned {}: {}".format(
                response.status_code,
                response.text,
            )
        )

    return response.json()


async def get_gmail_profile() -> Dict[str, Any]:
    return await _get(GMAIL_BASE_URL, "/users/me/profile")


async def get_calendar_list() -> Dict[str, Any]:
    return await _get(CALENDAR_BASE_URL, "/users/me/calendarList")


async def get_google_status() -> Dict[str, Any]:

    profile = await get_gmail_profile()
    calendars = await get_calendar_list()

    return {
        "connected": True,
        "email_address": profile.get("emailAddress"),
        "gmail_messages_total": profile.get("messagesTotal"),
        "calendar_count": len(calendars.get("items", [])),
    }
