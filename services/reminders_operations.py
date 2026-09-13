"""Apple Reminders synchronization bridge.

Provides authenticated API endpoints for iPhone-to-COS task synchronization.

Flow:
1. iPhone fetches pending sync (tasks created/updated in COS)
2. iPhone applies changes to Reminders
3. iPhone returns acknowledgment with actual reminder IDs/status
4. COS records the mapping and pending action status

Requires separate scoped REMINDERS_BRIDGE_API_KEY to avoid exposing full COS key.

NOTE: Apple Reminders doesn't expose a direct API. This bridge is designed to work
with Apple Shortcuts automation that exposes task state via HTTP requests.
"""

import json
import os
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from services.tasks import (
    create_task,
    update_task,
    get_task,
    TaskError,
)

def get_bridge_api_key() -> str:
    """Resolve the Reminders bridge key at runtime.

    Avoids stale values when tests mutate os.environ after import.
    """
    return os.getenv("REMINDERS_BRIDGE_API_KEY", "")


class RemindersError(Exception):
    pass


class RemindersConflictError(RemindersError):
    """Raised when concurrent edits detected."""
    pass


def validate_bridge_auth(provided_key: str) -> bool:
    """Verify the bridge API key.
    
    Different from main COS_API_KEY for principle of least privilege.
    Do not put user's full COS key on the phone.
    """
    
    bridge_key = get_bridge_api_key()

    if not bridge_key:
        raise RemindersError(
            "REMINDERS_BRIDGE_API_KEY not configured; Reminders sync unavailable"
        )

    if provided_key != bridge_key:
        raise RemindersError("Invalid Reminders bridge credential")
    
    return True


async def get_pending_sync(
    bridge_auth_key: str,
    last_sync_timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Get tasks pending synchronization to Reminders.
    
    Args:
        bridge_auth_key: Reminders bridge API key
        last_sync_timestamp: ISO datetime; return only newer tasks
    
    Returns:
        Dict with pending_actions (list of task snapshots to apply)
        and last_sync_timestamp (for next call)
    """
    
    validate_bridge_auth(bridge_auth_key)
    
    # TODO: Query database for tasks that:
    # - Have reminders_id (linked to Reminders)
    # - Have pending_commands (Shortcut hasn't acknowledged yet)
    # - Were updated since last_sync_timestamp
    
    # For now, return empty to indicate no pending actions
    return {
        "pending_actions": [],
        "last_sync_timestamp": datetime.now(timezone.utc).isoformat(),
        "sync_status": "ready",
    }


async def acknowledge_sync(
    bridge_auth_key: str,
    acknowledgments: List[Dict[str, Any]],
    approval_confirmed: bool = False,
) -> Dict[str, Any]:
    """Accept acknowledgments from Reminders of applied changes.

    Requires explicit approval to avoid silently mutating task state from the
    phone bridge without a human acknowledgment path.

    Args:
        bridge_auth_key: Reminders bridge API key
        acknowledgments: List of sync acknowledgments
        approval_confirmed: Explicit approval flag for device-confirmed changes

    Returns:
        Dict with synced count, conflicts, errors
    """

    validate_bridge_auth(bridge_auth_key)

    if not approval_confirmed:
        raise RemindersError(
            "Reminders acknowledgment requires explicit approval before task state can be updated."
        )

    synced = 0
    conflicts = []
    errors = []

    for ack in acknowledgments:
        try:
            task_id = ack.get("cos_task_id")

            if not task_id:
                errors.append("Missing cos_task_id in acknowledgment")
                continue

            # Get current task state
            task = get_task(task_id)

            # Check for concurrent edits
            server_version = task.get("sync_version", 1)
            device_version = ack.get("device_sync_version", 1)

            if device_version != server_version:
                # Device and server differ; this is a conflict
                conflicts.append({
                    "task_id": task_id,
                    "server_version": server_version,
                    "device_version": device_version,
                    "device_state": ack,
                    "server_state": task,
                })
                continue

            # Update task with Reminders mapping
            update_task(
                task_id,
                reminders_id=ack.get("reminders_id"),
                reminders_list=ack.get("reminders_list"),
                reminders_sync_version=device_version,
                reminders_last_sync=datetime.now(timezone.utc).isoformat(),
                pending_commands=[],  # Clear pending commands
                status="COMPLETE" if ack.get("is_completed") else "OPEN",
            )

            synced += 1

        except TaskError as e:
            errors.append(f"Task update failed: {e}")
        except Exception as e:
            errors.append(f"Unexpected error: {e}")

    return {
        "synced": synced,
        "conflicts": conflicts,
        "errors": errors,
        "approval_confirmed": True,
        "last_sync": datetime.now(timezone.utc).isoformat(),
    }


async def detect_reminders_conflict(
    task_id: int,
    device_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Detect if device and server have conflicting changes.
    
    Returns conflict info if versions differ, else None.
    """
    
    try:
        task = get_task(task_id)
    except TaskError:
        return None
    
    server_version = task.get("sync_version", 1)
    device_version = device_state.get("sync_version", 1)
    
    if server_version == device_version:
        return None
    
    # Versions differ - conflict detected
    return {
        "task_id": task_id,
        "server_version": server_version,
        "device_version": device_version,
        "server_title": task.get("title"),
        "device_title": device_state.get("title"),
        "server_due_date": task.get("deadline"),
        "device_due_date": device_state.get("due_date"),
        "server_priority": task.get("priority"),
        "device_priority": device_state.get("priority"),
        "server_completed": task.get("status") == "COMPLETE",
        "device_completed": device_state.get("is_completed", False),
    }


# Shortcut integration examples (for documentation)
SHORTCUT_SETUP_EXAMPLE = """
# Apple Shortcuts for COS Reminders Sync

## 1. OAuth / Auth Setup
- Acquire REMINDERS_BRIDGE_API_KEY from admin (different from main COS key)
- Store in iCloud Keychain or secure local storage
- Configure API_BASE_URL: https://wadadlitech-cos-api.onrender.com

## 2. Fetch Pending Tasks
POST /integrations/reminders/pending-sync
Headers: Authorization: Bearer {REMINDERS_BRIDGE_API_KEY}
Body: { "last_sync_timestamp": "2025-01-15T10:00:00Z" }

Response:
{
  "pending_actions": [
    {
      "action": "create",
      "cos_task_id": 123,
      "title": "Review proposal",
      "priority": 1,
      "due_date": "2025-01-15",
      "notes": "Email from Alice",
      "sync_version": 1
    }
  ],
  "last_sync_timestamp": "2025-01-15T10:30:00Z"
}

## 3. Apply to Reminders
- For each pending action, create/update reminder in Reminders app
- Capture: reminder ID, list name, priority as mapped, due date, completion status

## 4. Send Acknowledgment
POST /integrations/reminders/acknowledge
Headers: Authorization: Bearer {REMINDERS_BRIDGE_API_KEY}
Body:
{
  "acknowledgments": [
    {
      "cos_task_id": 123,
      "reminders_id": "REMINDERS-UUID",
      "reminders_list": "Personal",
      "title": "Review proposal",
      "priority": 1,
      "due_date": "2025-01-15",
      "is_completed": false,
      "sync_version": 1,
      "device_sync_version": 1
    }
  ]
}

Response:
{
  "synced": 1,
  "conflicts": [],
  "errors": [],
  "last_sync": "2025-01-15T10:30:15Z"
}

## 5. Error Handling
- If sync_version mismatch, return in conflicts; manual review required
- Retry on network errors (max 3 times, exponential backoff)
- Never delete reminders on omission from sync response
"""
