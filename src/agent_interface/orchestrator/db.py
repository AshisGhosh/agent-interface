"""Schema + connection management for orchestrator tables.

Tables live in the same SQLite file as the session registry but are owned
entirely by this module. Code outside `orchestrator/` should not SELECT or
mutate these tables directly.
"""

from __future__ import annotations

import sqlite3

from agent_interface.db import get_connection as _get_base_connection

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT,
    autonomy      TEXT NOT NULL DEFAULT 'none',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    archived_at   TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id                    TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL REFERENCES projects(id),
    parent_id             TEXT REFERENCES tasks(id),
    title                 TEXT NOT NULL,
    description           TEXT,
    status                TEXT NOT NULL DEFAULT 'backlog',
    priority              INTEGER NOT NULL DEFAULT 2,
    tags                  TEXT NOT NULL DEFAULT '',
    creator               TEXT NOT NULL DEFAULT 'user',
    spawned_from_task     TEXT,
    spawned_from_session  TEXT,
    assigned_session_id   TEXT,
    worktree_path         TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    closed_at             TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_status
    ON tasks(project_id, status);

CREATE TABLE IF NOT EXISTS task_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL REFERENCES tasks(id),
    event_type    TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'user',
    payload_json  TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task
    ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id              TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id   TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    author      TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create orchestrator tables if missing. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def get_connection() -> sqlite3.Connection:
    """Return a connection with both session and orchestrator schemas ready."""
    conn = _get_base_connection()
    ensure_schema(conn)
    return conn
