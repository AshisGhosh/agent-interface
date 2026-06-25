"""Per-project notebook — durable breadcrumbs agents leave for the next session.

Coding agents repeatedly land in a project, *figure something out* (the build
incantation, why an approach failed, the env var a test needs, "try X again, not
Y"), and then that knowledge dies with the session. The very next agent —
often literally told "try adding it again yourself" — rediscovers it from
scratch.

This is a tiny project-scoped notebook. An agent jots a note with
``agi note "<text>"`` and the next session reads them back with ``agi notes``.
It is deliberately distinct from the command runbook (:mod:`agent_interface.runlog`,
``agi run``): that journals *commands that ran*; this captures *freeform
knowledge* — gotchas, decisions, and "do this not that" hints.

Like the runbook it is project-scoped: the key is the git repo root (falling
back to the cwd), so notes are shared across subdirectories of a repo and notes
from different projects never mix. It works from any project directory.
"""

from __future__ import annotations

import time
from typing import Optional

# Project keying is shared with the runbook so `agi note`, `agi notes`, `agi run`
# and `agi runs` all agree on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    note        TEXT NOT NULL,
    tag         TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_notes_project ON project_notes(project);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def add_note(
    conn,
    *,
    project: str,
    note: str,
    tag: Optional[str] = None,
    created_at: Optional[float] = None,
) -> int:
    """Persist one note for *project* and return its row id."""
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO project_notes (project, note, tag, created_at) VALUES (?,?,?,?)",
        (project, note, tag, time.time() if created_at is None else created_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_notes(
    conn,
    project: str,
    *,
    tag: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
) -> list:
    """Most-recent-first notes for *project*, optionally filtered.

    *tag* matches exactly; *query* is a case-insensitive substring match on the
    note text. Both may be combined.
    """
    _ensure(conn)
    where = ["project=?"]
    params: list = [project]
    if tag is not None:
        where.append("tag=?")
        params.append(tag)
    if query is not None:
        where.append("LOWER(note) LIKE ?")
        params.append(f"%{query.lower()}%")
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM project_notes WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return list(rows)


def remove_note(conn, project: str, note_id: int) -> bool:
    """Delete one note by id, scoped to *project*. Returns True if a row went.

    Scoping the delete to the project means an id from another project's
    notebook can never remove the wrong note.
    """
    _ensure(conn)
    cur = conn.execute(
        "DELETE FROM project_notes WHERE id=? AND project=?",
        (note_id, project),
    )
    conn.commit()
    return cur.rowcount > 0
