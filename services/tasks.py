import os
import sqlite3
import json

from datetime import datetime, date
from typing import Any, Dict, List, Optional

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

# Make database path configurable; use $TMPDIR fallback for ephemeral deployments.
# For persistent storage, set COS_DATABASE_PATH explicitly or mount persistent volume.
DATABASE_PATH = os.getenv(
    "COS_DATABASE_PATH",
    os.path.join(DATA_DIR, "cos_tasks.db")
)


def get_database_path() -> str:
    """Resolve the active database path at runtime so tests and env overrides work.

    This avoids stale import-time config when a test sets COS_DATABASE_PATH after
    the module was first imported.
    """
    db_path = os.getenv("COS_DATABASE_PATH") or DATABASE_PATH
    if not db_path:
        db_path = DATABASE_PATH

    db_dir = os.path.dirname(db_path) or DATA_DIR
    os.makedirs(db_dir, exist_ok=True)
    return db_path


class TaskError(Exception):
    pass


def _ensure_data_directory():
    os.makedirs(
        DATA_DIR,
        exist_ok=True,
    )


def _get_connection():
    # Ensure the schema exists before the first task operation is attempted.
    try:
        from services.db_migrations import run_migrations
        run_migrations()
    except Exception:
        pass

    _ensure_data_directory()

    db_path = get_database_path()
    connection = sqlite3.connect(
        db_path
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def initialize_task_database():
    """Initialize database using migration system."""
    from services.db_migrations import run_migrations
    
    run_migrations()


def _row_to_dict(
    row: sqlite3.Row
) -> Dict[str, Any]:

    return {
        key: row[key]
        for key in row.keys()
    }


def create_task(
    title: str,
    description: Optional[str] = None,
    department: Optional[str] = None,
    project: Optional[str] = None,
    client: Optional[str] = None,
    owner: Optional[str] = "Jermain Gordon",
    priority: str = "NORMAL",
    due_date: Optional[str] = None,
    next_action: Optional[str] = None,
    blocker: Optional[str] = None,
    source: str = "COS",
    # New planning fields
    domain: Optional[str] = None,
    goal_links: Optional[List[str]] = None,
    estimated_minutes: Optional[int] = None,
    deadline: Optional[str] = None,
    planned_work_date: Optional[str] = None,
    dependencies: Optional[List[int]] = None,
    waiting_for_date: Optional[str] = None,
    postponement_count: int = 0,
    source_system: Optional[str] = None,
    external_id: Optional[str] = None,
    gmail_thread_id: Optional[str] = None,
    gmail_message_ids: Optional[List[str]] = None,
    calendar_event_id: Optional[str] = None,
    reminders_id: Optional[str] = None,
    reminders_list: Optional[str] = None,
    actual_duration_minutes: Optional[int] = None,
) -> Dict[str, Any]:

    if not title.strip():
        raise TaskError(
            "Task title is required."
        )

    now = datetime.utcnow().isoformat()

    priority = priority.upper()

    valid_priorities = [
        "CRITICAL",
        "HIGH",
        "NORMAL",
        "LOW",
        "BACKLOG",
    ]

    if priority not in valid_priorities:
        raise TaskError(
            "Invalid priority. Valid priorities: {}".format(
                ", ".join(
                    valid_priorities
                )
            )
        )

    connection = _get_connection()

    cursor = connection.cursor()

    # Convert list fields to JSON
    goal_links_json = json.dumps(goal_links) if goal_links else None
    dependencies_json = json.dumps(dependencies) if dependencies else None
    gmail_message_ids_json = json.dumps(gmail_message_ids) if gmail_message_ids else None

    cursor.execute(
        """
        INSERT INTO tasks (
            title,
            description,
            department,
            project,
            client,
            owner,
            priority,
            status,
            due_date,
            next_action,
            blocker,
            source,
            created_at,
            updated_at,
            domain,
            goal_links,
            estimated_minutes,
            deadline,
            planned_work_date,
            dependencies,
            waiting_for_date,
            postponement_count,
            source_system,
            external_id,
            gmail_thread_id,
            gmail_message_ids,
            calendar_event_id,
            reminders_id,
            reminders_list,
            actual_duration_minutes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            description,
            department,
            project,
            client,
            owner,
            priority,
            "OPEN",
            due_date,
            next_action,
            blocker,
            source,
            now,
            now,
            domain,
            goal_links_json,
            estimated_minutes,
            deadline,
            planned_work_date,
            dependencies_json,
            waiting_for_date,
            postponement_count,
            source_system,
            external_id,
            gmail_thread_id,
            gmail_message_ids_json,
            calendar_event_id,
            reminders_id,
            reminders_list,
            actual_duration_minutes,
        ),
    )

    task_id = cursor.lastrowid

    if task_id is None:
        connection.close()
        raise TaskError("Failed to create task.")

    connection.commit()

    connection.close()

    return get_task(task_id)


def get_task(
    task_id: int
) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    )

    row = cursor.fetchone()

    connection.close()

    if not row:
        raise TaskError(
            "Task {} not found.".format(
                task_id
            )
        )

    return _row_to_dict(row)


def get_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    department: Optional[str] = None,
    project: Optional[str] = None,
    client: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    sql = """
        SELECT *
        FROM tasks
        WHERE 1 = 1
    """

    values = []

    if status:
        sql += " AND status = ?"
        values.append(
            status.upper()
        )

    if priority:
        sql += " AND priority = ?"
        values.append(
            priority.upper()
        )

    if department:
        sql += " AND department = ?"
        values.append(
            department
        )

    if project:
        sql += " AND project = ?"
        values.append(
            project
        )

    if client:
        sql += " AND client = ?"
        values.append(
            client
        )

    sql += """
        ORDER BY
            CASE priority
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'NORMAL' THEN 3
                WHEN 'LOW' THEN 4
                WHEN 'BACKLOG' THEN 5
                ELSE 6
            END,
            due_date ASC,
            created_at ASC
        LIMIT ?
    """

    values.append(limit)

    cursor.execute(
        sql,
        values,
    )

    rows = cursor.fetchall()

    connection.close()

    tasks = [
        _row_to_dict(row)
        for row in rows
    ]

    return {
        "count": len(tasks),
        "tasks": tasks,
    }


def update_task(
    task_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    department: Optional[str] = None,
    project: Optional[str] = None,
    client: Optional[str] = None,
    owner: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    due_date: Optional[str] = None,
    next_action: Optional[str] = None,
    blocker: Optional[str] = None,
    # New planning fields
    domain: Optional[str] = None,
    goal_links: Optional[List[str]] = None,
    estimated_minutes: Optional[int] = None,
    deadline: Optional[str] = None,
    planned_work_date: Optional[str] = None,
    dependencies: Optional[List[int]] = None,
    waiting_for_date: Optional[str] = None,
    postponement_count: Optional[int] = None,
    source_system: Optional[str] = None,
    external_id: Optional[str] = None,
    gmail_thread_id: Optional[str] = None,
    gmail_message_ids: Optional[List[str]] = None,
    calendar_event_id: Optional[str] = None,
    reminders_id: Optional[str] = None,
    reminders_list: Optional[str] = None,
    sync_version: Optional[int] = None,
    pending_commands: Optional[List[Dict[str, Any]]] = None,
    conflict_flags: Optional[List[str]] = None,
    reminders_sync_version: Optional[int] = None,
    reminders_last_sync: Optional[str] = None,
    actual_duration_minutes: Optional[int] = None,
) -> Dict[str, Any]:

    existing = get_task(
        task_id
    )

    updates = {}

    fields = {
        "title": title,
        "description": description,
        "department": department,
        "project": project,
        "client": client,
        "owner": owner,
        "priority": priority,
        "status": status,
        "due_date": due_date,
        "next_action": next_action,
        "blocker": blocker,
        "domain": domain,
        "goal_links": json.dumps(goal_links) if goal_links is not None else None,
        "estimated_minutes": estimated_minutes,
        "deadline": deadline,
        "planned_work_date": planned_work_date,
        "dependencies": json.dumps(dependencies) if dependencies is not None else None,
        "waiting_for_date": waiting_for_date,
        "postponement_count": postponement_count,
        "source_system": source_system,
        "external_id": external_id,
        "gmail_thread_id": gmail_thread_id,
        "gmail_message_ids": json.dumps(gmail_message_ids) if gmail_message_ids is not None else None,
        "calendar_event_id": calendar_event_id,
        "reminders_id": reminders_id,
        "reminders_list": reminders_list,
        "sync_version": sync_version,
        "pending_commands": json.dumps(pending_commands) if pending_commands is not None else None,
        "conflict_flags": json.dumps(conflict_flags) if conflict_flags is not None else None,
        "reminders_sync_version": reminders_sync_version,
        "reminders_last_sync": reminders_last_sync,
        "actual_duration_minutes": actual_duration_minutes,
    }

    for key, value in fields.items():

        if value is not None:
            updates[key] = value

    if not updates:
        return existing

    if "priority" in updates:

        updates["priority"] = (
            updates["priority"].upper()
        )

    if "status" in updates:

        updates["status"] = (
            updates["status"].upper()
        )

    updates["updated_at"] = (
        datetime.utcnow().isoformat()
    )

    if (
        updates.get("status")
        == "COMPLETE"
    ):

        updates["completed_at"] = (
            datetime.utcnow().isoformat()
        )

    connection = _get_connection()

    cursor = connection.cursor()

    set_clause = ", ".join(
        "{} = ?".format(key)
        for key in updates.keys()
    )

    values = list(
        updates.values()
    )

    values.append(task_id)

    cursor.execute(
        """
        UPDATE tasks
        SET {}
        WHERE id = ?
        """.format(
            set_clause
        ),
        values,
    )

    connection.commit()

    connection.close()

    return get_task(
        task_id
    )


def complete_task(
    task_id: int
) -> Dict[str, Any]:

    return update_task(
        task_id,
        status="COMPLETE",
    )


def delete_task(
    task_id: int
) -> Dict[str, Any]:

    task = get_task(
        task_id
    )

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    )

    connection.commit()

    connection.close()

    return {
        "deleted": True,
        "task": task,
    }


def get_today_tasks() -> Dict[str, Any]:

    today = date.today().isoformat()

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE
            due_date = ?
            AND status != 'COMPLETE'
        ORDER BY
            CASE priority
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'NORMAL' THEN 3
                WHEN 'LOW' THEN 4
                ELSE 5
            END
        """,
        (today,),
    )

    rows = cursor.fetchall()

    connection.close()

    tasks = [
        _row_to_dict(row)
        for row in rows
    ]

    return {
        "date": today,
        "count": len(tasks),
        "tasks": tasks,
    }


def get_overdue_tasks() -> Dict[str, Any]:

    today = date.today().isoformat()

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE
            due_date IS NOT NULL
            AND due_date < ?
            AND status != 'COMPLETE'
        ORDER BY
            due_date ASC
        """,
        (today,),
    )

    rows = cursor.fetchall()

    connection.close()

    tasks = [
        _row_to_dict(row)
        for row in rows
    ]

    return {
        "as_of": today,
        "count": len(tasks),
        "tasks": tasks,
    }


def get_priority_tasks(
    limit: int = 10
) -> Dict[str, Any]:

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM tasks
        WHERE status != 'COMPLETE'
        ORDER BY
            CASE priority
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'NORMAL' THEN 3
                WHEN 'LOW' THEN 4
                WHEN 'BACKLOG' THEN 5
                ELSE 6
            END,
            due_date ASC,
            created_at ASC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()

    connection.close()

    tasks = [
        _row_to_dict(row)
        for row in rows
    ]

    return {
        "count": len(tasks),
        "tasks": tasks,
    }


# Create database/table automatically
# when the service is imported.
initialize_task_database()