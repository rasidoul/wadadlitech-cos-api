"""Database migration system for COS API.

Provides versioned, idempotent schema migrations that preserve existing data.
"""

import os
import sqlite3
from typing import Callable, Dict, List
from datetime import datetime

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


def get_database_path() -> str:
    """Resolve the runtime database path, honoring COS_DATABASE_PATH overrides."""
    db_path = os.getenv("COS_DATABASE_PATH") or DATABASE_PATH
    db_dir = os.path.dirname(db_path) or DATA_DIR
    os.makedirs(db_dir, exist_ok=True)
    return db_path


def _ensure_data_directory():
    os.makedirs(
        os.path.dirname(get_database_path()) or DATA_DIR,
        exist_ok=True,
    )


def _get_connection():
    _ensure_data_directory()

    connection = sqlite3.connect(
        get_database_path()
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def _get_current_schema_version() -> int:
    """Get the current schema version from the database.
    
    Returns 0 if the migrations table doesn't exist.
    """
    connection = _get_connection()
    cursor = connection.cursor()
    
    try:
        cursor.execute(
            """
            SELECT MAX(version) as max_version
            FROM schema_migrations
            """
        )
        row = cursor.fetchone()
        version = row["max_version"] if row and row["max_version"] else 0
        connection.close()
        return version
    except sqlite3.OperationalError:
        # migrations table doesn't exist yet
        connection.close()
        return 0


def _init_migrations_table():
    """Create the schema_migrations table if it doesn't exist."""
    connection = _get_connection()
    cursor = connection.cursor()
    
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    
    connection.commit()
    connection.close()


def _record_migration(version: int, name: str):
    """Record that a migration has been applied."""
    connection = _get_connection()
    cursor = connection.cursor()
    
    now = datetime.utcnow().isoformat()
    
    cursor.execute(
        """
        INSERT INTO schema_migrations (version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, now),
    )
    
    connection.commit()
    connection.close()


def _migration_001_initial_schema():
    """Initial tasks table schema (for legacy data)."""
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


def _migration_002_add_planning_fields():
    """Add planning and sync fields to tasks table."""
    connection = _get_connection()
    cursor = connection.cursor()
    
    # Get existing columns
    cursor.execute("PRAGMA table_info(tasks)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    
    # Add new columns only if they don't exist
    new_columns = {
        "domain": "TEXT DEFAULT 'PERSONAL'",  # 'PERSONAL' or 'BUSINESS'
        "goal_links": "TEXT",  # JSON array of goal/project IDs
        "estimated_minutes": "INTEGER",  # Estimated duration
        "deadline": "TEXT",  # Real deadline (ISO date)
        "planned_work_date": "TEXT",  # Planned execution date (ISO datetime)
        "dependencies": "TEXT",  # JSON array of task IDs this depends on
        "waiting_for_date": "TEXT",  # When to follow up on blocker (ISO date)
        "postponement_count": "INTEGER DEFAULT 0",  # Number of times postponed
        "source_system": "TEXT",  # System that generated task (gmail, calendar, etc.)
        "external_id": "TEXT",  # Stable ID from source system (email thread ID, etc.)
        "gmail_thread_id": "TEXT",  # Gmail thread ID if from Gmail
        "gmail_message_ids": "TEXT",  # JSON array of Gmail message IDs
        "calendar_event_id": "TEXT",  # Google Calendar event ID if linked
        "sync_version": "INTEGER DEFAULT 1",  # Version for conflict detection
        "last_sync_time": "TEXT",  # Last successful sync time (ISO datetime)
        "pending_commands": "TEXT",  # JSON array of pending external commands
        "conflict_flags": "TEXT",  # JSON array of conflict markers
        "reminders_id": "TEXT",  # Apple Reminders ID if synced
        "reminders_list": "TEXT",  # Apple Reminders list name
        "reminders_sync_version": "INTEGER DEFAULT 0",  # Reminders sync version
        "reminders_last_sync": "TEXT",  # Last Reminders sync time
        "actual_duration_minutes": "INTEGER",  # User-reported actual duration
    }
    
    for col_name, col_def in new_columns.items():
        if col_name not in existing_columns:
            cursor.execute(
                f"""
                ALTER TABLE tasks
                ADD COLUMN {col_name} {col_def}
                """
            )
    
    connection.commit()
    connection.close()


def _migration_003_add_reminder_indexes():
    """Add indexes for common query patterns."""
    connection = _get_connection()
    cursor = connection.cursor()
    
    indexes = [
        ("idx_tasks_status", "tasks(status)"),
        ("idx_tasks_priority", "tasks(priority)"),
        ("idx_tasks_due_date", "tasks(due_date)"),
        ("idx_tasks_source", "tasks(source)"),
        ("idx_tasks_external_id", "tasks(external_id)"),
        ("idx_tasks_gmail_thread_id", "tasks(gmail_thread_id)"),
        ("idx_tasks_calendar_event_id", "tasks(calendar_event_id)"),
        ("idx_tasks_reminders_id", "tasks(reminders_id)"),
        ("idx_tasks_project_status", "tasks(project, status)"),
        ("idx_tasks_domain_status", "tasks(domain, status)"),
    ]
    
    for index_name, index_def in indexes:
        try:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}"
            )
        except sqlite3.OperationalError:
            # Index might already exist
            pass
    
    connection.commit()
    connection.close()


# Registry of all migrations in order
MIGRATIONS: List[tuple[int, str, Callable]] = [
    (1, "initial_schema", _migration_001_initial_schema),
    (2, "add_planning_fields", _migration_002_add_planning_fields),
    (3, "add_reminder_indexes", _migration_003_add_reminder_indexes),
]


def run_migrations():
    """Run all pending migrations in order.
    
    Idempotent - safe to call multiple times.
    """
    _init_migrations_table()
    
    current_version = _get_current_schema_version()
    
    for version, name, migration_func in MIGRATIONS:
        if version > current_version:
            try:
                migration_func()
                _record_migration(version, name)
                print(f"✓ Applied migration {version}: {name}")
            except Exception as e:
                print(f"✗ Failed to apply migration {version}: {name}")
                raise


def backup_database() -> str:
    """Create a backup of the current database.
    
    Returns the path to the backup file.
    """
    import shutil
    
    if not os.path.exists(DATABASE_PATH):
        return None
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DATABASE_PATH}.backup.{timestamp}"
    
    shutil.copy2(DATABASE_PATH, backup_path)
    
    return backup_path
