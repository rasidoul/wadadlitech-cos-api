import json
import os
import sqlite3

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "data"
)

# Overridable so automated tests never touch the production database.
DATABASE_PATH = os.getenv(
    "AGENT_BRIEFS_DB_PATH"
) or os.path.join(
    DATA_DIR,
    "agent_briefs.db"
)

MAX_SECTIONS_PAYLOAD_BYTES = 200_000

VALID_PRIORITIES = [
    "CRITICAL",
    "HIGH",
    "NORMAL",
    "LOW",
]

VALID_STATUSES = [
    "QUEUED",
    "DELIVERED",
    "ACKNOWLEDGED",
    "IN_PROGRESS",
    "COMPLETED",
    "DELIVERY_FAILED",
]

# Lifecycle: QUEUED -> DELIVERED -> ACKNOWLEDGED -> IN_PROGRESS -> COMPLETED
# with DELIVERY_FAILED reachable from QUEUED/DELIVERED and retryable back
# into QUEUED/DELIVERED.
ALLOWED_TRANSITIONS = {
    "QUEUED": {"DELIVERED", "DELIVERY_FAILED", "ACKNOWLEDGED"},
    "DELIVERED": {"ACKNOWLEDGED", "DELIVERY_FAILED"},
    "ACKNOWLEDGED": {"IN_PROGRESS", "COMPLETED"},
    "IN_PROGRESS": {"COMPLETED"},
    "DELIVERY_FAILED": {"QUEUED", "DELIVERED"},
    "COMPLETED": set(),
}


class AgentBriefError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _ensure_data_directory():
    os.makedirs(
        os.path.dirname(DATABASE_PATH) or ".",
        exist_ok=True,
    )


def _get_connection():
    _ensure_data_directory()

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def initialize_agent_brief_database():

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            agent_id TEXT NOT NULL,

            source_agent_id TEXT NOT NULL,

            title TEXT NOT NULL,

            summary TEXT,

            brief_type TEXT NOT NULL DEFAULT 'INTELLIGENCE_BRIEF',

            priority TEXT NOT NULL DEFAULT 'NORMAL',

            reporting_period_start TEXT,

            reporting_period_end TEXT,

            sections_json TEXT NOT NULL,

            practical_move TEXT,

            tags_json TEXT NOT NULL DEFAULT '[]',

            requires_human_approval INTEGER NOT NULL DEFAULT 0,

            metadata_json TEXT,

            status TEXT NOT NULL DEFAULT 'QUEUED',

            delivery_status TEXT NOT NULL DEFAULT 'PENDING',

            delivery_attempts INTEGER NOT NULL DEFAULT 0,

            delivery_error TEXT,

            idempotency_key TEXT,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL,

            delivered_at TEXT,

            acknowledged_at TEXT,

            completed_at TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_agent_briefs_idempotency
        ON agent_briefs (agent_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )

    connection.commit()

    connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row: sqlite3.Row) -> Dict[str, Any]:

    record = {
        key: row[key]
        for key in row.keys()
    }

    record["sections"] = json.loads(
        record.pop("sections_json") or "[]"
    )

    record["tags"] = json.loads(
        record.pop("tags_json") or "[]"
    )

    metadata_json = record.pop("metadata_json")

    record["metadata"] = (
        json.loads(metadata_json)
        if metadata_json
        else None
    )

    record["requires_human_approval"] = bool(
        record["requires_human_approval"]
    )

    return record


def _validate_priority(priority: str) -> str:

    normalized = (priority or "").upper()

    if normalized not in VALID_PRIORITIES:
        raise AgentBriefError(
            "Invalid priority. Valid priorities: {}".format(
                ", ".join(VALID_PRIORITIES)
            ),
            status_code=422,
        )

    return normalized


def _validate_status(status: str) -> str:

    normalized = (status or "").upper()

    if normalized not in VALID_STATUSES:
        raise AgentBriefError(
            "Invalid status. Valid statuses: {}".format(
                ", ".join(VALID_STATUSES)
            ),
            status_code=422,
        )

    return normalized


def _validate_sections(sections: Any) -> List[Dict[str, Any]]:

    if not isinstance(sections, list) or not sections:
        raise AgentBriefError(
            "sections must be a non-empty list of objects.",
            status_code=422,
        )

    for section in sections:

        if not isinstance(section, dict):
            raise AgentBriefError(
                "Each brief section must be an object.",
                status_code=422,
            )

        sources = section.get("sources")

        if sources is not None:

            if not isinstance(sources, list):
                raise AgentBriefError(
                    "Section 'sources' must be a list.",
                    status_code=422,
                )

            for source in sources:

                if not isinstance(source, dict):
                    raise AgentBriefError(
                        "Each source must be an object.",
                        status_code=422,
                    )

                url = source.get("url")

                if url and not (
                    str(url).startswith("http://")
                    or str(url).startswith("https://")
                ):
                    raise AgentBriefError(
                        "Source URLs must start with http:// or https://.",
                        status_code=422,
                    )

    payload_size = len(
        json.dumps(sections).encode("utf-8")
    )

    if payload_size > MAX_SECTIONS_PAYLOAD_BYTES:
        raise AgentBriefError(
            "Brief sections payload is too large.",
            status_code=413,
        )

    return sections


def _find_by_idempotency_key(
    agent_id: str,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT * FROM agent_briefs
        WHERE agent_id = ? AND idempotency_key = ?
        """,
        (agent_id, idempotency_key),
    )

    row = cursor.fetchone()

    connection.close()

    if not row:
        return None

    return _row_to_record(row)


async def _deliver_to_webhook(
    agent_id: str,
    brief: Dict[str, Any],
) -> Dict[str, Any]:
    """Attempt delivery to the agent's configured HighLevel webhook.

    Never accepts a caller-supplied URL; only the admin-configured
    environment variable is used, to prevent SSRF via the API.
    """

    from config.agents import get_webhook_env_var

    env_var = get_webhook_env_var(agent_id)

    webhook_url = os.getenv(env_var) if env_var else None

    if not webhook_url:
        return {
            "attempted": False,
            "delivery_status": "PENDING_CONFIGURATION",
            "delivery_error": None,
        }

    payload = {
        "brief_id": brief["id"],
        "agent_id": brief["agent_id"],
        "source_agent_id": brief["source_agent_id"],
        "title": brief["title"],
        "summary": brief["summary"],
        "brief_type": brief["brief_type"],
        "priority": brief["priority"],
        "reporting_period_start": brief["reporting_period_start"],
        "reporting_period_end": brief["reporting_period_end"],
        "sections": brief["sections"],
        "practical_move": brief["practical_move"],
        "tags": brief["tags"],
        "requires_human_approval": brief["requires_human_approval"],
        "metadata": brief["metadata"],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
            )

        if 200 <= response.status_code < 300:
            return {
                "attempted": True,
                "delivery_status": "DELIVERED",
                "delivery_error": None,
            }

        return {
            "attempted": True,
            "delivery_status": "FAILED",
            "delivery_error": (
                "Webhook delivery returned HTTP {}.".format(
                    response.status_code
                )
            ),
        }

    except httpx.HTTPError:
        return {
            "attempted": True,
            "delivery_status": "FAILED",
            "delivery_error": "Webhook delivery request failed.",
        }


def _apply_delivery_result(
    brief_id: int,
    delivery_result: Dict[str, Any],
) -> None:

    now = _now()

    delivery_status = delivery_result["delivery_status"]

    updates = {
        "delivery_status": delivery_status,
        "updated_at": now,
    }

    if delivery_result["attempted"]:
        updates["delivery_attempts_increment"] = True

    if delivery_status == "DELIVERED":
        updates["status"] = "DELIVERED"
        updates["delivered_at"] = now
        updates["delivery_error"] = None

    elif delivery_status == "FAILED":
        updates["status"] = "DELIVERY_FAILED"
        updates["delivery_error"] = delivery_result["delivery_error"]

    _apply_updates(brief_id, updates)


def _apply_updates(
    brief_id: int,
    updates: Dict[str, Any],
) -> None:

    increment_attempts = updates.pop(
        "delivery_attempts_increment",
        False,
    )

    connection = _get_connection()

    cursor = connection.cursor()

    set_clauses = [
        "{} = ?".format(key)
        for key in updates.keys()
    ]

    values = list(updates.values())

    if increment_attempts:
        set_clauses.append(
            "delivery_attempts = delivery_attempts + 1"
        )

    values.append(brief_id)

    cursor.execute(
        """
        UPDATE agent_briefs
        SET {}
        WHERE id = ?
        """.format(
            ", ".join(set_clauses)
        ),
        values,
    )

    connection.commit()

    connection.close()


async def create_brief(
    agent_id: str,
    source_agent_id: str,
    title: str,
    summary: Optional[str],
    brief_type: str,
    priority: str,
    reporting_period_start: Optional[str],
    reporting_period_end: Optional[str],
    sections: List[Dict[str, Any]],
    practical_move: Optional[str],
    tags: List[str],
    requires_human_approval: bool,
    metadata: Optional[Dict[str, Any]],
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:

    if not title or not title.strip():
        raise AgentBriefError(
            "Brief title is required.",
            status_code=422,
        )

    if not brief_type or not brief_type.strip():
        raise AgentBriefError(
            "brief_type is required.",
            status_code=422,
        )

    priority = _validate_priority(priority)

    sections = _validate_sections(sections)

    if idempotency_key:

        existing = _find_by_idempotency_key(
            agent_id,
            idempotency_key,
        )

        if existing:
            return existing

    now = _now()

    connection = _get_connection()

    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO agent_briefs (
                agent_id,
                source_agent_id,
                title,
                summary,
                brief_type,
                priority,
                reporting_period_start,
                reporting_period_end,
                sections_json,
                practical_move,
                tags_json,
                requires_human_approval,
                metadata_json,
                status,
                delivery_status,
                delivery_attempts,
                idempotency_key,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                source_agent_id,
                title.strip(),
                summary,
                brief_type,
                priority,
                reporting_period_start,
                reporting_period_end,
                json.dumps(sections),
                practical_move,
                json.dumps(tags or []),
                1 if requires_human_approval else 0,
                json.dumps(metadata) if metadata is not None else None,
                "QUEUED",
                "PENDING",
                0,
                idempotency_key,
                now,
                now,
            ),
        )

        brief_id = cursor.lastrowid

        connection.commit()

    except sqlite3.IntegrityError:
        connection.close()

        # Another request already created a brief for this idempotency key.
        existing = _find_by_idempotency_key(
            agent_id,
            idempotency_key,
        )

        if existing:
            return existing

        raise AgentBriefError(
            "Failed to create brief due to a duplicate idempotency key.",
            status_code=409,
        )

    connection.close()

    # Brief is durably stored before any delivery attempt is made.
    delivery_result = await _deliver_to_webhook(
        agent_id,
        get_brief(agent_id, brief_id),
    )

    _apply_delivery_result(
        brief_id,
        delivery_result,
    )

    return get_brief(agent_id, brief_id)


def get_brief(
    agent_id: str,
    brief_id: int,
) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT * FROM agent_briefs
        WHERE id = ? AND agent_id = ?
        """,
        (brief_id, agent_id),
    )

    row = cursor.fetchone()

    connection.close()

    if not row:
        raise AgentBriefError(
            "Brief {} not found for agent {}.".format(
                brief_id,
                agent_id,
            ),
            status_code=404,
        )

    return _row_to_record(row)


def list_briefs(
    agent_id: str,
    status: Optional[str] = None,
    brief_type: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    sql = """
        SELECT * FROM agent_briefs
        WHERE agent_id = ?
    """

    values: List[Any] = [agent_id]

    if status:
        sql += " AND status = ?"
        values.append(_validate_status(status))

    if brief_type:
        sql += " AND brief_type = ?"
        values.append(brief_type)

    if priority:
        sql += " AND priority = ?"
        values.append(_validate_priority(priority))

    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"

    values.append(limit)
    values.append(offset)

    cursor.execute(sql, values)

    rows = cursor.fetchall()

    connection.close()

    briefs = [
        _row_to_record(row)
        for row in rows
    ]

    return {
        "agent_id": agent_id,
        "count": len(briefs),
        "briefs": briefs,
    }


def update_brief_fields(
    agent_id: str,
    brief_id: int,
    **fields: Any,
) -> Dict[str, Any]:

    existing = get_brief(agent_id, brief_id)

    updates: Dict[str, Any] = {}

    if "title" in fields and fields["title"] is not None:

        if not fields["title"].strip():
            raise AgentBriefError(
                "Brief title cannot be blank.",
                status_code=422,
            )

        updates["title"] = fields["title"].strip()

    if "summary" in fields and fields["summary"] is not None:
        updates["summary"] = fields["summary"]

    if "priority" in fields and fields["priority"] is not None:
        updates["priority"] = _validate_priority(fields["priority"])

    if "practical_move" in fields and fields["practical_move"] is not None:
        updates["practical_move"] = fields["practical_move"]

    if "tags" in fields and fields["tags"] is not None:
        updates["tags_json"] = json.dumps(fields["tags"])

    if (
        "requires_human_approval" in fields
        and fields["requires_human_approval"] is not None
    ):
        updates["requires_human_approval"] = (
            1 if fields["requires_human_approval"] else 0
        )

    if "metadata" in fields and fields["metadata"] is not None:
        updates["metadata_json"] = json.dumps(fields["metadata"])

    if "status" in fields and fields["status"] is not None:

        new_status = _validate_status(fields["status"])

        current_status = existing["status"]

        if new_status != current_status:

            allowed = ALLOWED_TRANSITIONS.get(current_status, set())

            if new_status not in allowed:
                raise AgentBriefError(
                    "Cannot transition brief from {} to {}.".format(
                        current_status,
                        new_status,
                    ),
                    status_code=409,
                )

        updates["status"] = new_status

        if new_status == "ACKNOWLEDGED":
            updates["acknowledged_at"] = _now()

        if new_status == "COMPLETED":
            updates["completed_at"] = _now()

    if not updates:
        return existing

    updates["updated_at"] = _now()

    _apply_updates(brief_id, updates)

    return get_brief(agent_id, brief_id)


def acknowledge_brief(
    agent_id: str,
    brief_id: int,
) -> Dict[str, Any]:

    existing = get_brief(agent_id, brief_id)

    if existing["status"] not in ("QUEUED", "DELIVERED"):
        raise AgentBriefError(
            "Brief cannot be acknowledged from status {}.".format(
                existing["status"]
            ),
            status_code=409,
        )

    now = _now()

    _apply_updates(
        brief_id,
        {
            "status": "ACKNOWLEDGED",
            "acknowledged_at": now,
            "updated_at": now,
        },
    )

    return get_brief(agent_id, brief_id)


def complete_brief(
    agent_id: str,
    brief_id: int,
) -> Dict[str, Any]:

    existing = get_brief(agent_id, brief_id)

    if existing["status"] not in ("ACKNOWLEDGED", "IN_PROGRESS"):
        raise AgentBriefError(
            "Brief cannot be completed from status {}.".format(
                existing["status"]
            ),
            status_code=409,
        )

    now = _now()

    _apply_updates(
        brief_id,
        {
            "status": "COMPLETED",
            "completed_at": now,
            "updated_at": now,
        },
    )

    return get_brief(agent_id, brief_id)


async def retry_delivery(
    agent_id: str,
    brief_id: int,
) -> Dict[str, Any]:

    existing = get_brief(agent_id, brief_id)

    if existing["status"] not in ("QUEUED", "DELIVERY_FAILED"):
        raise AgentBriefError(
            "Delivery can only be retried from QUEUED or DELIVERY_FAILED "
            "(current status: {}).".format(existing["status"]),
            status_code=409,
        )

    delivery_result = await _deliver_to_webhook(
        agent_id,
        existing,
    )

    _apply_delivery_result(
        brief_id,
        delivery_result,
    )

    return get_brief(agent_id, brief_id)


def get_brief_stats(agent_id: str) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT status, COUNT(*) as count
        FROM agent_briefs
        WHERE agent_id = ?
        GROUP BY status
        """,
        (agent_id,),
    )

    counts = {
        row["status"]: row["count"]
        for row in cursor.fetchall()
    }

    cursor.execute(
        """
        SELECT * FROM agent_briefs
        WHERE agent_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (agent_id,),
    )

    latest_row = cursor.fetchone()

    connection.close()

    latest_brief = None

    if latest_row:
        latest_record = _row_to_record(latest_row)

        latest_brief = {
            "id": latest_record["id"],
            "title": latest_record["title"],
            "status": latest_record["status"],
            "delivery_status": latest_record["delivery_status"],
            "created_at": latest_record["created_at"],
        }

    pending = counts.get("QUEUED", 0) + counts.get("DELIVERED", 0)

    return {
        "agent_id": agent_id,
        "pending_briefs": pending,
        "delivery_failures": counts.get("DELIVERY_FAILED", 0),
        "acknowledged_briefs": counts.get("ACKNOWLEDGED", 0),
        "in_progress_briefs": counts.get("IN_PROGRESS", 0),
        "completed_briefs": counts.get("COMPLETED", 0),
        "latest_brief": latest_brief,
    }


# Create database/table automatically when the service is imported.
initialize_agent_brief_database()
