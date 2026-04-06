"""SQLite database setup and connection management."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    label         TEXT,
    host          TEXT,
    cwd           TEXT,
    repo_root     TEXT,
    branch        TEXT,
    tmux_session  TEXT,
    tmux_window   TEXT,
    tmux_pane     TEXT,
    worktree_path TEXT,
    pid           INTEGER,
    is_managed    INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL DEFAULT 'unknown',
    summary       TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    archived_at   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    event_type   TEXT NOT NULL,
    payload_json TEXT,
    created_at   TEXT NOT NULL
);
"""


def _default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "agent-interface" / "registry.db"


def get_db_path() -> Path:
    env = os.environ.get("AGI_DB_PATH")
    if env:
        return Path(env)
    return _default_db_path()


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialize) a SQLite connection."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may not exist in older databases."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "last_tool" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_tool TEXT")
    if "tool_count" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN tool_count INTEGER NOT NULL DEFAULT 0")
