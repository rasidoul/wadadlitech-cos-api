import os
import sqlite3

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

DATABASE_PATH = os.path.join(
    DATA_DIR,
    "cos_tasks.db"
)


class TaskError(Exception):
    pass


def _ensure_data_directory():
    os.makedirs(
        DATA_DIR,
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


def initialize_task_database():

    connection = _get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            description TEXT,

            department TEXT,

            project TEXT,

            client TEXT,

            owner TEXT,

            priority TEXT NOT NULL DEFAULT 'NORMAL',

            status TEXT NOT NULL DEFAULT 'OPEN',

            due_date TEXT,

            next_action TEXT,

            blocker TEXT,

            source TEXT DEFAULT 'COS',

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL,

            completed_at TEXT
        )
        """
    )

    connection.commit()

    connection.close()


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
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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