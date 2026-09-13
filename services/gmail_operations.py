"""Gmail operations for personal/business planning.

Provides:
- Paginated message/thread search
- Label management (list, create, apply, remove, archive)
- Draft creation, updating (preserve threading)
- Explicit draft sending (human-approved only) with duplicate protection
- Email-to-task conversion with thread linkage
"""

import base64
import json
import urllib.parse
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import httpx

from services.google_auth_enhanced import get_access_token, GoogleAuthError, GoogleConnectionError

GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
GMAIL_USERS_ME = f"{GMAIL_BASE_URL}/users/me"

# Track sent message IDs to prevent duplicate sends
_RECENTLY_SENT: Dict[str, float] = {}
DUPLICATE_SEND_WINDOW_SECONDS = 60


class GmailError(Exception):
    pass


async def _gmail_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    """Async HTTP request to Gmail API with automatic auth.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH)
        path: Path relative to /users/me (e.g., "/messages", "/drafts/123")
        params: Query parameters
        json_body: JSON request body
        timeout: Request timeout in seconds
    
    Returns:
        Parsed JSON response
    
    Raises:
        GmailError: If the request fails
    """
    
    try:
        access_token = await get_access_token()
    except (GoogleAuthError, GoogleConnectionError) as e:
        raise GmailError(f"Failed to get access token: {e}")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    
    url = f"{GMAIL_USERS_ME}{path}"
    
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
        raise GmailError(f"Request failed: {e}")
    
    if response.status_code >= 400:
        raise GmailError(
            f"Gmail API {response.status_code}: {response.text[:500]}"
        )
    
    return response.json()


async def search_messages(
    query: Optional[str] = None,
    max_results: int = 10,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for Gmail messages.
    
    Args:
        query: Gmail search query (e.g., "is:request", "from:user@example.com")
        max_results: Number of results (1-100)
        page_token: Token for pagination
    
    Returns:
        Dict with messages list and nextPageToken (if more results)
    """
    
    max_results = max(1, min(max_results, 100))
    
    params = {"maxResults": max_results}
    
    if query:
        params["q"] = query
    
    if page_token:
        params["pageToken"] = page_token
    
    result = await _gmail_request("GET", "/messages", params=params)
    
    # Populate full message details
    messages = []
    for msg_summary in result.get("messages", []):
        try:
            full_msg = await get_message(msg_summary["id"])
            messages.append(full_msg)
        except GmailError:
            # If we can't fetch full message, include summary
            messages.append(msg_summary)
    
    return {
        "messages": messages,
        "nextPageToken": result.get("nextPageToken"),
        "resultSizeEstimate": result.get("resultSizeEstimate"),
    }


async def get_message(
    message_id: str,
    format: str = "full",
) -> Dict[str, Any]:
    """Fetch a complete message.
    
    Args:
        message_id: Gmail message ID
        format: "full", "minimal", or "raw"
    
    Returns:
        Message dict with id, threadId, snippet, payload (headers/body), labels, etc.
    """
    
    params = {"format": format}
    
    return await _gmail_request(
        "GET",
        f"/messages/{urllib.parse.quote(message_id, safe='')}",
        params=params,
    )


async def get_thread(
    thread_id: str,
    format: str = "full",
) -> Dict[str, Any]:
    """Fetch a complete thread.
    
    Args:
        thread_id: Gmail thread ID
        format: "full", "minimal", or "raw"
    
    Returns:
        Thread dict with id, messages list
    """
    
    params = {"format": format}
    
    return await _gmail_request(
        "GET",
        f"/threads/{urllib.parse.quote(thread_id, safe='')}",
        params=params,
    )


async def list_labels() -> Dict[str, Any]:
    """List all Gmail labels."""
    
    result = await _gmail_request("GET", "/labels")
    
    return {
        "labels": result.get("labels", []),
    }


async def create_label(
    label_name: str,
) -> Dict[str, Any]:
    """Create a new Gmail label.
    
    Args:
        label_name: Label name (e.g., "COS Tasks", "Personal/Follow-up")
    
    Returns:
        Created label dict with id, name, etc.
    """
    
    return await _gmail_request(
        "POST",
        "/labels",
        json_body={
            "name": label_name,
            "labelListVisibility": "labelShow",  # Visible in label list
            "messageListVisibility": "show",      # Messages shown
        },
    )


async def apply_label(
    message_id: str,
    label_id: str,
) -> Dict[str, Any]:
    """Apply a label to a message."""
    
    return await _gmail_request(
        "POST",
        f"/messages/{urllib.parse.quote(message_id, safe='')}/modify",
        json_body={"addLabelIds": [label_id]},
    )


async def remove_label(
    message_id: str,
    label_id: str,
) -> Dict[str, Any]:
    """Remove a label from a message."""
    
    return await _gmail_request(
        "POST",
        f"/messages/{urllib.parse.quote(message_id, safe='')}/modify",
        json_body={"removeLabelIds": [label_id]},
    )


async def archive_message(message_id: str) -> Dict[str, Any]:
    """Archive a message (remove from Inbox)."""
    
    return await _gmail_request(
        "POST",
        f"/messages/{urllib.parse.quote(message_id, safe='')}/modify",
        json_body={"removeLabelIds": ["INBOX"]},
    )


async def create_draft(
    to: str,
    subject: str,
    body: str,
    thread_id: Optional[str] = None,
    in_reply_to_message_id: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a draft message, optionally in reply to an existing thread.
    
    Args:
        to: Recipient email address
        subject: Subject line
        body: Message body (plain text)
        thread_id: If set, creates draft in this thread (for reply)
        in_reply_to_message_id: Message ID to reply to
        cc: CC recipients
        bcc: BCC recipients
    
    Returns:
        Draft dict with id, message dict, etc.
    """
    
    # Build RFC 2822 message
    headers = []
    headers.append(f"To: {to}")
    if cc:
        headers.append(f"Cc: {', '.join(cc)}")
    if bcc:
        headers.append(f"Bcc: {', '.join(bcc)}")
    headers.append(f"Subject: {subject}")
    
    if in_reply_to_message_id:
        headers.append(f"In-Reply-To: {in_reply_to_message_id}")
    
    raw_message = "\r\n".join(headers) + "\r\n\r\n" + body
    
    # Encode as base64url
    raw_bytes = raw_message.encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")
    
    draft_body = {
        "message": {
            "raw": encoded,
        }
    }
    
    if thread_id:
        draft_body["message"]["threadId"] = thread_id
    
    return await _gmail_request(
        "POST",
        "/drafts",
        json_body=draft_body,
    )


async def update_draft(
    draft_id: str,
    to: str,
    subject: str,
    body: str,
) -> Dict[str, Any]:
    """Update an existing draft.
    
    Args:
        draft_id: Draft ID
        to: Recipient email
        subject: Subject line
        body: Message body
    
    Returns:
        Updated draft dict
    """
    
    headers = [
        f"To: {to}",
        f"Subject: {subject}",
    ]
    
    raw_message = "\r\n".join(headers) + "\r\n\r\n" + body
    raw_bytes = raw_message.encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")
    
    return await _gmail_request(
        "PUT",
        f"/drafts/{urllib.parse.quote(draft_id, safe='')}",
        json_body={
            "id": draft_id,
            "message": {
                "raw": encoded,
            },
        },
    )


async def send_draft(
    draft_id: str,
    approval_confirmed: bool = False,
    recipient: Optional[str] = None,
    subject: Optional[str] = None,
    body_preview: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a draft message.

    Requires explicit human approval before sending mail. This guard prevents the
    API from sending email based solely on a drafted message or a request body.

    Args:
        draft_id: Draft ID
        approval_confirmed: Must be set explicitly to True by a human-approved action
        recipient: Optional recipient used for logging and approval checks
        subject: Optional subject for audit logging
        body_preview: Optional preview for audit logging

    Returns:
        Sent message dict with id, threadId, etc.

    Raises:
        GmailError: If send fails or approval is missing
    """

    if not approval_confirmed:
        raise GmailError(
            "Email send requires explicit human approval before the recipient and content can be sent."
        )

    # Check for duplicate send
    now = datetime.now(timezone.utc).timestamp()

    if draft_id in _RECENTLY_SENT:
        last_sent = _RECENTLY_SENT[draft_id]
        if now - last_sent < DUPLICATE_SEND_WINDOW_SECONDS:
            raise GmailError(
                f"Draft {draft_id} was sent {int(now - last_sent)}s ago; "
                f"duplicate send prevented"
            )

    result = await _gmail_request(
        "POST",
        f"/drafts/send",
        json_body={"id": draft_id},
    )

    # Record send time
    _RECENTLY_SENT[draft_id] = now

    return {
        **result,
        "approval_confirmed": True,
        "audit": {
            "draft_id": draft_id,
            "recipient": recipient,
            "subject": subject,
            "body_preview": (body_preview[:200] if body_preview else None),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def get_email_context(message_id: str) -> Dict[str, Any]:
    """Extract relevant context from an email for task creation.
    
    Returns context that helps distinguish requests, deadlines, FYI, etc.
    Never includes full body content (sanitized).
    
    Args:
        message_id: Gmail message ID
    
    Returns:
        Dict with from, subject, snippet, received_date, is_starred, etc.
    """
    
    msg = await get_message(message_id, format="full")
    
    headers = msg.get("payload", {}).get("headers", [])
    header_dict = {h["name"]: h["value"] for h in headers}
    
    snippet = msg.get("snippet", "")
    
    # Limit snippet to prevent huge responses
    snippet = snippet[:500] if snippet else ""
    
    return {
        "message_id": message_id,
        "thread_id": msg.get("threadId"),
        "from": header_dict.get("From"),
        "to": header_dict.get("To"),
        "subject": header_dict.get("Subject"),
        "date": header_dict.get("Date"),
        "snippet": snippet,
        "is_starred": "STARRED" in msg.get("labelIds", []),
        "is_unread": "UNREAD" in msg.get("labelIds", []),
        "labels": msg.get("labelIds", []),
    }
