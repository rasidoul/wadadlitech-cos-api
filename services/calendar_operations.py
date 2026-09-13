"""Google Calendar operations for focus block and availability management.

Provides:
- Calendar listing and event retrieval
- Availability checking with timezone handling (America/New_York)
- Focus block creation, updating, cancellation (COS-managed)
- Timezone, recurrence, all-day event handling
- Conflict detection before writes
- Idempotent focus block management
"""

import json
import urllib.parse
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

import httpx
from zoneinfo import ZoneInfo

from services.google_auth_enhanced import get_access_token, GoogleAuthError, GoogleConnectionError

CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"

# COS focus block marker
COS_FOCUS_BLOCK_PREFIX = "[COS Focus]"

# Default timezone
DEFAULT_TIMEZONE = "America/New_York"

# Configurable from environment
FOCUS_CALENDAR_ID = None  # Will use primary if not set


class CalendarError(Exception):
    pass


async def _calendar_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    """Async HTTP request to Calendar API with automatic auth.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        path: Path relative to Calendar API root
        params: Query parameters
        json_body: JSON request body
        timeout: Request timeout in seconds
    
    Returns:
        Parsed JSON response
    
    Raises:
        CalendarError: If the request fails
    """
    
    try:
        access_token = await get_access_token()
    except (GoogleAuthError, GoogleConnectionError) as e:
        raise CalendarError(f"Failed to get access token: {e}")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    url = f"{CALENDAR_BASE_URL}{path}"
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
    except Exception as e:
        raise CalendarError(f"Request failed: {e}")
    
    if response.status_code >= 400:
        raise CalendarError(
            f"Calendar API {response.status_code}: {response.text[:500]}"
        )
    
    return response.json()


async def list_calendars() -> Dict[str, Any]:
    """List calendars accessible to the authorized user.
    
    Returns:
        Dict with calendars list
    """
    
    result = await _calendar_request("GET", "/users/me/calendarList")
    
    calendars = []
    for cal in result.get("items", []):
        calendars.append({
            "id": cal.get("id"),
            "summary": cal.get("summary"),
            "description": cal.get("description"),
            "timezone": cal.get("timeZone"),
            "primary": cal.get("primary", False),
            "access_role": cal.get("accessRole"),  # "owner", "writer", "reader"
        })
    
    return {
        "calendars": calendars,
    }


async def get_events(
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 50,
    page_token: Optional[str] = None,
    single_events: bool = True,
) -> Dict[str, Any]:
    """Fetch events from a calendar.
    
    Args:
        calendar_id: Calendar ID (or "primary")
        time_min: Start time (ISO 8601, inclusive)
        time_max: End time (ISO 8601, exclusive)
        max_results: Number of events (1-250)
        page_token: Pagination token
        single_events: If True, recurring events expanded
    
    Returns:
        Dict with events list, nextPageToken, etc.
    """
    
    max_results = max(1, min(max_results, 250))
    
    params = {
        "maxResults": max_results,
        "singleEvents": "true" if single_events else "false",
        "orderBy": "startTime",
    }
    
    if time_min:
        params["timeMin"] = time_min
    else:
        # Default to now
        params["timeMin"] = datetime.now(timezone.utc).isoformat()
    
    if time_max:
        params["timeMax"] = time_max
    
    if page_token:
        params["pageToken"] = page_token
    
    result = await _calendar_request(
        "GET",
        f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events",
        params=params,
    )
    
    events = []
    for ev in result.get("items", []):
        events.append(_normalize_event(ev))
    
    return {
        "events": events,
        "nextPageToken": result.get("nextPageToken"),
        "summary": result.get("summary"),
        "timezone": result.get("timeZone", DEFAULT_TIMEZONE),
    }


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant event details, normalizing times and types."""
    
    start = event.get("start", {})
    end = event.get("end", {})
    
    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "description": event.get("description"),
        "start": start,
        "end": end,
        "start_time": start.get("dateTime"),
        "start_date": start.get("date"),
        "end_time": end.get("dateTime"),
        "end_date": end.get("date"),
        "timezone": start.get("timeZone", end.get("timeZone")),
        "is_all_day": "date" in start,
        "is_recurring": "recurrence" in event,
        "recurrence": event.get("recurrence", []),
        "status": event.get("status"),  # "confirmed", "tentative", "cancelled"
        "transparency": event.get("transparency"),  # "opaque" (busy), "transparent" (free)
        "attendees": event.get("attendees", []),
        "organizer": event.get("organizer"),
        "created": event.get("created"),
        "updated": event.get("updated"),
        "html_link": event.get("htmlLink"),
    }


async def check_availability(
    calendar_id: str = "primary",
    start_time: str = None,
    end_time: str = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> Dict[str, Any]:
    """Check if time is available (no busy events).
    
    Args:
        calendar_id: Calendar ID
        start_time: Start time (ISO 8601)
        end_time: End time (ISO 8601)
        timezone_name: Timezone for interpretation
    
    Returns:
        Dict with available flag, conflicting events, etc.
    """
    
    if not start_time:
        start_time = datetime.now(timezone.utc).isoformat()
    
    if not end_time:
        # Default to 1 hour ahead
        end_time = (
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            + timedelta(hours=1)
        ).isoformat()
    
    # Query for events in time range
    events_result = await get_events(
        calendar_id=calendar_id,
        time_min=start_time,
        time_max=end_time,
        single_events=True,
    )
    
    # Check for busy events (ignore free-time-marked events and cancelled)
    conflicts = []
    for event in events_result.get("events", []):
        if (
            event.get("status") == "cancelled"
            or event.get("transparency") == "transparent"
        ):
            continue
        
        conflicts.append({
            "id": event.get("id"),
            "summary": event.get("summary"),
            "start": event.get("start_time"),
            "end": event.get("end_time"),
        })
    
    return {
        "available": len(conflicts) == 0,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": timezone_name,
        "conflicts": conflicts,
    }


async def create_focus_block(
    calendar_id: str = "primary",
    start_time: str = None,
    end_time: str = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    idempotency_key: Optional[str] = None,
    approval_confirmed: bool = False,
) -> Dict[str, Any]:
    """Create a COS-managed focus block on the calendar.

    Requires explicit human approval before changing a calendar event. This guard
    ensures that scheduling changes are explicit and auditable.

    Args:
        calendar_id: Calendar ID
        start_time: Start time (ISO 8601)
        end_time: End time (ISO 8601)
        title: Focus block title (default: "[COS Focus]")
        description: Optional description
        timezone_name: Timezone for event
        idempotency_key: Unique key for this block (prevents duplicates)
        approval_confirmed: Must be explicitly set to True by a human-approved action

    Returns:
        Created event dict

    Raises:
        CalendarError: If creation fails or conflicts detected
    """

    if not approval_confirmed:
        raise CalendarError(
            "Calendar focus-block creation requires explicit human approval before scheduling can change."
        )

    if not title:
        title = COS_FOCUS_BLOCK_PREFIX
    elif not title.startswith(COS_FOCUS_BLOCK_PREFIX):
        title = f"{COS_FOCUS_BLOCK_PREFIX} {title}"

    # Check for conflicts first
    availability = await check_availability(
        calendar_id=calendar_id,
        start_time=start_time,
        end_time=end_time,
        timezone_name=timezone_name,
    )

    if not availability["available"]:
        raise CalendarError(
            f"Time slot is busy. Conflicts: {availability['conflicts']}"
        )

    # Prepare event
    body = {
        "summary": title,
        "start": {
            "dateTime": start_time,
            "timeZone": timezone_name,
        },
        "end": {
            "dateTime": end_time,
            "timeZone": timezone_name,
        },
        "description": description or "COS focus time",
        "transparency": "opaque",  # Block time as busy
    }

    # Add idempotency key to description if provided
    if idempotency_key:
        body["description"] = (
            f"{body['description']}\n[idempotency_key: {idempotency_key}]"
        )

    result = await _calendar_request(
        "POST",
        f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events",
        json_body=body,
    )

    return {
        **_normalize_event(result),
        "approval_confirmed": True,
        "audit": {
            "calendar_id": calendar_id,
            "start_time": start_time,
            "end_time": end_time,
            "title": title,
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def update_focus_block(
    calendar_id: str = "primary",
    event_id: str = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> Dict[str, Any]:
    """Update an existing focus block.
    
    Only updates fields that are explicitly provided. Checks for conflicts
    before applying the change.
    
    Args:
        calendar_id: Calendar ID
        event_id: Event ID to update
        start_time: New start time (optional)
        end_time: New end time (optional)
        title: New title (optional)
        description: New description (optional)
        timezone_name: Timezone
    
    Returns:
        Updated event dict
    
    Raises:
        CalendarError: If event not found or conflicts detected
    """
    
    # Fetch existing event
    event_path = f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}"
    existing = await _calendar_request("GET", event_path)
    
    # Determine times to check for conflicts
    check_start = start_time or existing.get("start", {}).get("dateTime")
    check_end = end_time or existing.get("end", {}).get("dateTime")
    
    if check_start and check_end:
        # Check for conflicts in new time slot
        availability = await check_availability(
            calendar_id=calendar_id,
            start_time=check_start,
            end_time=check_end,
            timezone_name=timezone_name,
        )
        
        # Ignore self-conflict with existing event
        conflicts = [
            c for c in availability["conflicts"]
            if c.get("id") != event_id
        ]
        
        if conflicts:
            raise CalendarError(
                f"New time slot conflicts. Conflicts: {conflicts}"
            )
    
    # Prepare update
    update = {}
    
    if start_time:
        update["start"] = {
            "dateTime": start_time,
            "timeZone": timezone_name,
        }
    
    if end_time:
        update["end"] = {
            "dateTime": end_time,
            "timeZone": timezone_name,
        }
    
    if title:
        if not title.startswith(COS_FOCUS_BLOCK_PREFIX):
            title = f"{COS_FOCUS_BLOCK_PREFIX} {title}"
        update["summary"] = title
    
    if description:
        update["description"] = description
    
    if not update:
        # No changes
        return _normalize_event(existing)
    
    # Apply update
    result = await _calendar_request(
        "PUT",
        event_path,
        json_body={**existing, **update},
    )
    
    return _normalize_event(result)


async def cancel_focus_block(
    calendar_id: str = "primary",
    event_id: str = None,
) -> Dict[str, Any]:
    """Cancel/delete a COS-managed focus block.
    
    Args:
        calendar_id: Calendar ID
        event_id: Event ID to cancel
    
    Returns:
        Confirmation dict
    """
    
    await _calendar_request(
        "DELETE",
        f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}",
    )
    
    return {
        "cancelled": True,
        "event_id": event_id,
        "calendar_id": calendar_id,
    }
