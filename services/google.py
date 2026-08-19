import asyncio
import logging
import os
import urllib.parse

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wadadlitech.google")
logger.setLevel(logging.INFO)

if not logger.handlers:
    # Ensure startup diagnostics are visible on Render/uvicorn regardless of
    # the root logger configuration, without hijacking other loggers.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"
DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"

# Least-privilege read-only scopes. These must match what was actually
# granted when GOOGLE_REFRESH_TOKEN was issued (see scripts/test_google_connection.py).
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google Docs/Sheets/Slides have no raw bytes; they must be exported.
_EXPORTABLE_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

MAX_DRIVE_CONTENT_CHARS = 20_000

_SANITIZED_ERROR_CODES = (
    "invalid_grant",
    "invalid_client",
    "unauthorized_client",
    "access_denied",
    "insufficient",
    "invalid_token",
)


class GoogleError(Exception):
    pass


def _require_config():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured."
        )

    if not GOOGLE_REFRESH_TOKEN:
        raise GoogleError("GOOGLE_REFRESH_TOKEN is not configured.")


def get_google_config_status() -> Dict[str, bool]:
    """Presence-only check for required env vars. Never returns values."""

    return {
        "GOOGLE_CLIENT_ID": bool(GOOGLE_CLIENT_ID),
        "GOOGLE_CLIENT_SECRET": bool(GOOGLE_CLIENT_SECRET),
        "GOOGLE_REFRESH_TOKEN": bool(GOOGLE_REFRESH_TOKEN),
    }


def is_google_configured() -> bool:
    return all(get_google_config_status().values())


def log_google_startup_diagnostics() -> None:
    """Log safe (non-secret) configuration state at application startup."""

    config_status = get_google_config_status()

    for var_name, present in config_status.items():
        logger.info(
            "Google %s configured: %s",
            var_name,
            "YES" if present else "NO",
        )

    missing = [
        var_name
        for var_name, present in config_status.items()
        if not present
    ]

    if missing:
        for var_name in missing:
            logger.warning(
                "%s is not configured. Google services will be unavailable.",
                var_name,
            )
        return

    logger.info("Google Gmail integration configured: YES")
    logger.info("Google Calendar integration configured: YES")
    logger.info("Google Drive integration configured: YES")


def _sanitize_error(exc: Exception) -> str:
    """Reduce an exception to a short, non-sensitive diagnostic code.

    Never returns full response bodies, tokens, or secrets.
    """

    message = str(exc)

    for code in _SANITIZED_ERROR_CODES:
        if code in message:
            return code

    if "token refresh failed" in message:
        return "token_refresh_failed"

    if "not configured" in message:
        return "not_configured"

    return "connection_failed"


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


async def _get(
    base_url: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    access_token = await _get_access_token()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            "{}{}".format(base_url, path),
            headers={
                "Authorization": "Bearer {}".format(access_token)
            },
            params=params,
        )

    if response.status_code >= 400:
        raise GoogleError(
            "Google returned {}: {}".format(
                response.status_code,
                response.text,
            )
        )

    return response.json()


# =========================================================
# GMAIL
# =========================================================

async def get_gmail_profile() -> Dict[str, Any]:
    return await _get(GMAIL_BASE_URL, "/users/me/profile")


async def search_gmail_messages(
    query: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:

    params: Dict[str, Any] = {
        "maxResults": max(1, min(limit, 50)),
    }

    if query:
        params["q"] = query

    return await _get(
        GMAIL_BASE_URL,
        "/users/me/messages",
        params=params,
    )


async def get_gmail_message(
    message_id: str,
    message_format: str = "metadata",
) -> Dict[str, Any]:

    return await _get(
        GMAIL_BASE_URL,
        "/users/me/messages/{}".format(message_id),
        params={"format": message_format},
    )


# =========================================================
# CALENDAR
# =========================================================

async def get_calendar_list() -> Dict[str, Any]:
    return await _get(CALENDAR_BASE_URL, "/users/me/calendarList")


async def search_calendar_events(
    calendar_id: str = "primary",
    query: Optional[str] = None,
    limit: int = 10,
    time_min: Optional[str] = None,
) -> Dict[str, Any]:

    params: Dict[str, Any] = {
        "maxResults": max(1, min(limit, 50)),
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": (
            time_min
            or datetime.now(timezone.utc).isoformat()
        ),
    }

    if query:
        params["q"] = query

    return await _get(
        CALENDAR_BASE_URL,
        "/calendars/{}/events".format(
            urllib.parse.quote(calendar_id, safe="")
        ),
        params=params,
    )


# =========================================================
# DRIVE
# =========================================================

async def search_drive_files(
    query: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:

    drive_query = "trashed = false"

    if query:
        safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
        drive_query += " and fullText contains '{}'".format(safe_query)

    params = {
        "pageSize": max(1, min(limit, 50)),
        "q": drive_query,
        "orderBy": "modifiedTime desc",
        "fields": (
            "files(id,name,mimeType,modifiedTime,size,webViewLink),"
            "nextPageToken"
        ),
    }

    return await _get(DRIVE_BASE_URL, "/files", params=params)


async def get_drive_file(file_id: str) -> Dict[str, Any]:

    return await _get(
        DRIVE_BASE_URL,
        "/files/{}".format(file_id),
        params={
            "fields": (
                "id,name,mimeType,modifiedTime,size,webViewLink,owners"
            )
        },
    )


async def get_drive_file_content(file_id: str) -> Dict[str, Any]:
    """Best-effort read of a Drive file's text content.

    Google Docs/Sheets/Slides are exported as plain text/CSV. Other
    binary file types are not supported for a text preview.
    """

    metadata = await get_drive_file(file_id)
    mime_type = metadata.get("mimeType", "")
    access_token = await _get_access_token()

    if mime_type in _EXPORTABLE_MIME_TYPES:
        request_url = "{}/files/{}/export".format(DRIVE_BASE_URL, file_id)
        request_params = {"mimeType": _EXPORTABLE_MIME_TYPES[mime_type]}

    elif mime_type.startswith("application/vnd.google-apps"):
        raise GoogleError(
            "Drive file type '{}' is not supported for content export.".format(
                mime_type
            )
        )

    else:
        request_url = "{}/files/{}".format(DRIVE_BASE_URL, file_id)
        request_params = {"alt": "media"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            request_url,
            params=request_params,
            headers={
                "Authorization": "Bearer {}".format(access_token)
            },
        )

    if response.status_code >= 400:
        raise GoogleError(
            "Google Drive returned {}.".format(response.status_code)
        )

    full_text = response.text

    return {
        "file_id": file_id,
        "name": metadata.get("name"),
        "mime_type": mime_type,
        "content": full_text[:MAX_DRIVE_CONTENT_CHARS],
        "truncated": len(full_text) > MAX_DRIVE_CONTENT_CHARS,
    }


# =========================================================
# STATUS / DIAGNOSTICS
# =========================================================

async def get_google_status() -> Dict[str, Any]:
    """Legacy combined Gmail + Calendar status (kept for backward compatibility)."""

    profile = await get_gmail_profile()
    calendars = await get_calendar_list()

    return {
        "connected": True,
        "email_address": profile.get("emailAddress"),
        "gmail_messages_total": profile.get("messagesTotal"),
        "calendar_count": len(calendars.get("items", [])),
    }


async def check_service_connection(check_callable) -> Dict[str, Any]:
    """Run a minimal read-only call and return a sanitized connectivity result.

    Never raises; never returns raw Google response bodies or credentials.
    """

    try:
        await check_callable()
        return {"configured": True, "connected": True}

    except GoogleError as exc:
        return {
            "configured": True,
            "connected": False,
            "error": _sanitize_error(exc),
        }


async def check_gmail_connection() -> Dict[str, Any]:
    return await check_service_connection(get_gmail_profile)


async def check_calendar_connection() -> Dict[str, Any]:
    return await check_service_connection(get_calendar_list)


async def check_drive_connection() -> Dict[str, Any]:
    return await check_service_connection(lambda: search_drive_files(limit=1))


async def get_google_integration_status() -> Dict[str, Any]:
    """Sanitized per-service connectivity report for GET /integrations/google/status."""

    config_status = get_google_config_status()

    if not all(config_status.values()):
        unavailable = {
            "configured": False,
            "connected": False,
            "error": "not_configured",
        }

        return {
            "configured": False,
            "authentication": "oauth2",
            "missing_variables": [
                var_name
                for var_name, present in config_status.items()
                if not present
            ],
            "gmail": unavailable,
            "calendar": unavailable,
            "drive": unavailable,
        }

    gmail_result, calendar_result, drive_result = await asyncio.gather(
        check_gmail_connection(),
        check_calendar_connection(),
        check_drive_connection(),
    )

    return {
        "configured": True,
        "authentication": "oauth2",
        "gmail": gmail_result,
        "calendar": calendar_result,
        "drive": drive_result,
    }
