"""Enhanced Google authentication and diagnostics for expanded scopes.

Supports token caching, write operations, and comprehensive diagnostics.
Intended to be used alongside the existing google.py module.

IMPORTANT SCOPE NOTE:
The scopes listed here represent what COS aims to use, but the GOOGLE_REFRESH_TOKEN
must have been obtained with these exact scopes granted. If scopes are changed here
but the token wasn't re-authorized with those scopes, write operations will fail.

To re-authorize after changing scopes:
1. Update SCOPES below with desired scopes
2. Run: python scripts/test_google_connection.py
3. Complete the OAuth consent flow
4. Copy the new GOOGLE_REFRESH_TOKEN to .env
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

TOKEN_URL = "https://oauth2.googleapis.com/token"

# Expanded scopes for personal/business planning:
# - Gmail: read, label, archive, draft creation/update, explicit send
# - Calendar: read, write (create/update focus blocks), list
# - Drive: keep existing read-only
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",  # Read messages
    "https://www.googleapis.com/auth/gmail.labels",     # Manage labels
    "https://www.googleapis.com/auth/gmail.modify",     # Modify labels, archive
    "https://www.googleapis.com/auth/gmail.draft",      # Manage drafts
    "https://www.googleapis.com/auth/gmail.send",       # Explicit send (human-approved only)
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",  # Full calendar read/write for focus blocks
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",  # Preserve existing read
]

ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES + DRIVE_SCOPES


class GoogleAuthError(Exception):
    """Authentication/authorization error."""
    pass


class GoogleConnectionError(Exception):
    """Connection or API error."""
    pass


# Simple in-memory token cache (per-process; not shared across instances)
_TOKEN_CACHE: Dict[str, Any] = {
    "access_token": None,
    "expires_at": None,
}


async def _get_cached_access_token() -> Optional[str]:
    """Return cached access token if still valid, else None."""
    
    if (
        _TOKEN_CACHE.get("access_token") is None
        or _TOKEN_CACHE.get("expires_at") is None
    ):
        return None
    
    # Return if at least 5 minutes remain (buffer for request time)
    if time.time() < _TOKEN_CACHE["expires_at"] - 300:
        return _TOKEN_CACHE["access_token"]
    
    return None


async def _refresh_access_token() -> str:
    """Refresh the access token using the refresh token.
    
    Raises GoogleAuthError if the refresh token is missing or invalid.
    Raises GoogleConnectionError if the token endpoint is unreachable.
    """
    
    if not GOOGLE_REFRESH_TOKEN:
        raise GoogleAuthError("GOOGLE_REFRESH_TOKEN not configured")
    
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleAuthError("GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not configured")
    
    try:
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
    except Exception as e:
        raise GoogleConnectionError(f"Token refresh request failed: {e}")
    
    if response.status_code == 400 and "invalid_grant" in response.text:
        raise GoogleAuthError("Refresh token is invalid or revoked; re-authorization required")
    
    if response.status_code >= 400:
        raise GoogleConnectionError(
            f"Token refresh failed {response.status_code}: {response.text}"
        )
    
    data = response.json()
    access_token = data.get("access_token")
    expires_in = data.get("expires_in", 3600)
    
    if not access_token:
        raise GoogleConnectionError("No access token in response")
    
    # Cache with expiry time
    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = time.time() + expires_in
    
    return access_token


async def get_access_token() -> str:
    """Get a valid access token, using cache if available, else refreshing."""
    
    cached = await _get_cached_access_token()
    if cached:
        return cached
    
    return await _refresh_access_token()


async def get_google_config_status() -> Dict[str, bool]:
    """Return configuration presence (safe, non-secret check)."""
    
    return {
        "GOOGLE_CLIENT_ID": bool(GOOGLE_CLIENT_ID),
        "GOOGLE_CLIENT_SECRET": bool(GOOGLE_CLIENT_SECRET),
        "GOOGLE_REFRESH_TOKEN": bool(GOOGLE_REFRESH_TOKEN),
    }


async def check_google_read_access() -> bool:
    """Test if we can read from Google APIs (Gmail profile fetch)."""
    
    try:
        token = await get_access_token()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
        
        return response.status_code == 200
    
    except Exception:
        return False


async def get_gmail_auth_status() -> Dict[str, Any]:
    """Detailed Gmail authorization status."""
    
    config = await get_google_config_status()
    
    if not all(config.values()):
        return {
            "configured": False,
            "reason": "Missing configuration",
            "can_read": False,
            "can_send": False,
            "granted_scopes": [],
        }
    
    can_read = await check_google_read_access()
    
    return {
        "configured": True,
        "can_read": can_read,
        "can_send": can_read,  # Send capability determined by scope, not connection test
        "last_check": datetime.now(timezone.utc).isoformat(),
        "note": "Send capability depends on authorization scope at token issuance; not verified by this check",
    }


async def get_calendar_auth_status() -> Dict[str, Any]:
    """Detailed Calendar authorization status."""
    
    config = await get_google_config_status()
    
    if not all(config.values()):
        return {
            "configured": False,
            "reason": "Missing configuration",
            "can_read": False,
            "can_write": False,
            "granted_scopes": [],
        }
    
    can_read = await check_google_read_access()
    
    return {
        "configured": True,
        "can_read": can_read,
        "can_write": can_read,  # Write capability determined by scope, not connection test
        "last_check": datetime.now(timezone.utc).isoformat(),
        "note": "Write capability depends on authorization scope at token issuance; not verified by this check",
    }


async def get_google_integration_diagnostics() -> Dict[str, Any]:
    """Comprehensive, safe diagnostic of Google integration state.
    
    Never includes actual tokens or full error messages.
    Suitable for logging and user-facing status endpoints.
    """
    
    config = await get_google_config_status()
    gmail_status = await get_gmail_auth_status()
    calendar_status = await get_calendar_auth_status()
    
    return {
        "configured": all(config.values()),
        "config": config,
        "gmail": gmail_status,
        "calendar": calendar_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "required_scopes_for_send": GMAIL_SCOPES + ["send"],
        "required_scopes_for_calendar_write": CALENDAR_SCOPES,
        "reauthorization_note": (
            "If you need to enable send, calendar write, or other new capabilities, "
            "you must re-run the Google OAuth flow to obtain a new GOOGLE_REFRESH_TOKEN "
            "with expanded scopes. Simply changing the SCOPES constants is insufficient."
        ),
    }
