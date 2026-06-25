"""Session registry — CRUD operations backed by SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from agent_interface.models import Event, Session, _now_utc
from agent_interface.states import ACTIVE_STATES, STALE_THRESHOLD_SECONDS, SessionState

# ── helpers ──────────────────────────────────────────────────────────────────

def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        label=row["label"],
        host=row["host"],
        cwd=row["cwd"],
        repo_root=row["repo_root"],
        branch=row["branch"],
        tmux_session=row["tmux_session"],
        tmux_window=row["tmux_window"],
        tmux_pane=row["tmux_pane"],
        worktree_path=row["worktree_path"],
        pid=row["pid"],
        is_managed=bool(row["is_managed"]),
        state=row["state"],
        summary=row["summary"],
        last_tool=row["last_tool"],
        tool_count=row["tool_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
        archived_at=row["archived_at"],
    )


def _pid_alive(pid: int) -> bool:
    """Check whether a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True


def _maybe_reap(conn: sqlite3.Connection, session: Session) -> Session:
    """If the session has a pid and that process is dead, mark it done."""
    if session.pid is None:
        return session
    if session.state in (SessionState.DONE, SessionState.ARCHIVED):
        return session
    if _pid_alive(session.pid):
        return session

    # Process is gone — auto-close.
    now = _now_utc()
    conn.execute(
        "UPDATE sessions SET state=?, updated_at=?, archived_at=NULL WHERE id=?",
        (SessionState.DONE, now, session.id),
    )
    conn.execute(
        "INSERT INTO events (session_id, event_type, payload_json, created_at) VALUES (?,?,?,?)",
        (session.id, "auto_closed", json.dumps({"reason": "pid_exited", "pid": session.pid}), now),
    )
    conn.commit()
    session.state = SessionState.DONE
    session.updated_at = now
    return session


def _is_stale(session: Session) -> bool:
    """Determine if an active session should be considered stale."""
    if session.state not in ACTIVE_STATES:
        return False
    try:
        last = datetime.fromisoformat(session.last_seen_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    # Tolerate naive timestamps (e.g. from SQLite datetime('now')) by
    # treating them as UTC. Mixing naive + aware would raise TypeError.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age > STALE_THRESHOLD_SECONDS


# ── public API ───────────────────────────────────────────────────────────────

def register_session(conn: sqlite3.Connection, session: Session) -> Session:
    """Insert a new session into the registry."""
    conn.execute(
        """INSERT INTO sessions
           (id, label, host, cwd, repo_root, branch,
            tmux_session, tmux_window, tmux_pane, worktree_path,
            pid, is_managed, state, summary, last_tool, tool_count,
            created_at, updated_at, last_seen_at, archived_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session.id, session.label, session.host, session.cwd,
            session.repo_root, session.branch,
            session.tmux_session, session.tmux_window, session.tmux_pane,
            session.worktree_path, session.pid, int(session.is_managed),
            session.state, session.summary, session.last_tool, session.tool_count,
            session.created_at, session.updated_at, session.last_seen_at,
            session.archived_at,
        ),
    )
    _append_event(conn, session.id, "session_registered")
    conn.commit()
    return session


def find_session(
    conn: sqlite3.Connection, query: str, *, active_only: bool = False,
) -> list[Session]:
    """Find sessions matching a query against id, label, cwd, or pid.

    Returns all matches (caller decides how to handle 0, 1, or many). When
    ``active_only`` is set, terminal sessions (done/archived) are excluded —
    used for reply routing so a query can't resolve to a dead session whose
    tmux pane no longer exists.
    """
    terminal = (SessionState.DONE, SessionState.ARCHIVED)

    # Exact ID match first.
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (query,)).fetchone()
    if row is not None:
        session = _maybe_reap(conn, _row_to_session(row))
        if active_only and session.state in terminal:
            return []
        return [session]

    # Search across fields with LIKE.
    state_clause = (
        "AND state NOT IN ('archived','done')" if active_only else "AND state != 'archived'"
    )
    pattern = f"%{query}%"
    rows = conn.execute(
        f"""SELECT * FROM sessions
           WHERE (id LIKE ? OR label LIKE ? OR cwd LIKE ? OR CAST(pid AS TEXT) = ?)
             {state_clause}
           ORDER BY updated_at DESC""",
        (pattern, pattern, pattern, query),
    ).fetchall()
    results = [_maybe_reap(conn, _row_to_session(r)) for r in rows]
    if active_only:
        results = [s for s in results if s.state not in terminal]
    return results


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[Session]:
    """Fetch a single session by exact id, with pid liveness check."""
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        return None
    session = _row_to_session(row)
    return _maybe_reap(conn, session)


def list_sessions(
    conn: sqlite3.Connection,
    *,
    include_done: bool = False,
    include_archived: bool = False,
) -> list[Session]:
    """Return active sessions, auto-reaping dead pids and annotating stale."""
    rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    results: list[Session] = []
    for row in rows:
        session = _row_to_session(row)
        session = _maybe_reap(conn, session)

        # Filter out unwanted states.
        if session.state == SessionState.ARCHIVED and not include_archived:
            continue
        if session.state == SessionState.DONE and not include_done:
            continue

        results.append(session)
    return results


def list_waiting(conn: sqlite3.Connection) -> list[Session]:
    """Return sessions in waiting_for_user state."""
    rows = conn.execute(
        "SELECT * FROM sessions WHERE state=? ORDER BY updated_at DESC",
        (SessionState.WAITING_FOR_USER,),
    ).fetchall()
    return [_row_to_session(row) for row in rows]


def update_state(
    conn: sqlite3.Connection, session_id: str, new_state: str,
) -> Optional[Session]:
    """Change a session's state."""
    # Validate state value.
    SessionState(new_state)

    now = _now_utc()
    cur = conn.execute(
        "UPDATE sessions SET state=?, updated_at=?, last_seen_at=? WHERE id=?",
        (new_state, now, now, session_id),
    )
    if cur.rowcount == 0:
        return None
    _append_event(conn, session_id, "state_changed", {"new_state": new_state})
    conn.commit()
    return get_session(conn, session_id)


def rename_session(
    conn: sqlite3.Connection, session_id: str, label: str,
) -> Optional[Session]:
    """Set or change a session's label."""
    now = _now_utc()
    cur = conn.execute(
        "UPDATE sessions SET label=?, updated_at=? WHERE id=?",
        (label, now, session_id),
    )
    if cur.rowcount == 0:
        return None
    _append_event(conn, session_id, "renamed", {"label": label})
    conn.commit()
    return get_session(conn, session_id)


def archive_session(conn: sqlite3.Connection, session_id: str) -> Optional[Session]:
    """Soft-archive a session."""
    now = _now_utc()
    cur = conn.execute(
        "UPDATE sessions SET state=?, archived_at=?, updated_at=? WHERE id=?",
        (SessionState.ARCHIVED, now, now, session_id),
    )
    if cur.rowcount == 0:
        return None
    _append_event(conn, session_id, "archived")
    conn.commit()
    return get_session(conn, session_id)


def restore_session(conn: sqlite3.Connection, session_id: str) -> Optional[Session]:
    """Restore an archived session back to idle."""
    now = _now_utc()
    cur = conn.execute(
        "UPDATE sessions SET state=?, archived_at=NULL, updated_at=?, last_seen_at=? WHERE id=?",
        (SessionState.IDLE, now, now, session_id),
    )
    if cur.rowcount == 0:
        return None
    _append_event(conn, session_id, "restored")
    conn.commit()
    return get_session(conn, session_id)


def is_stale(session: Session) -> bool:
    """Public accessor for stale check."""
    return _is_stale(session)


def reconcile(conn: sqlite3.Connection) -> dict[str, int]:
    """Reconcile the registry against live processes — the self-healing tick.

    For every non-terminal session, verify its pid still points at a live
    agent process. Reap sessions whose process has exited (classic stale) or
    whose pid was recycled by an unrelated process (pid reuse → phantom
    'running' that would otherwise live forever). Runs deliberately (via
    ``agi doctor`` / the heartbeat), not on every read, so the conservative
    auto-reap on the hot path stays simple.

    Returns a summary: {"checked", "reaped_exited", "reaped_reused"}.
    """
    from agent_interface.scan import _pid_identity

    summary = {"checked": 0, "reaped_exited": 0, "reaped_reused": 0}
    rows = conn.execute(
        "SELECT * FROM sessions WHERE state NOT IN ('done','archived')",
    ).fetchall()

    for row in rows:
        session = _row_to_session(row)
        if session.pid is None:
            continue
        summary["checked"] += 1

        alive = _pid_alive(session.pid)
        # Identity is only meaningful for a live pid; None means "can't tell"
        # (no /proc / permission) → leave it alone to avoid false reaping.
        reused = alive and _pid_identity(session.pid) is False

        if alive and not reused:
            continue

        reason = "pid_exited" if not alive else "pid_reused"
        now = _now_utc()
        conn.execute(
            "UPDATE sessions SET state=?, updated_at=? WHERE id=?",
            (SessionState.DONE, now, session.id),
        )
        _append_event(conn, session.id, "auto_closed", {"reason": reason, "pid": session.pid})
        if alive:
            summary["reaped_reused"] += 1
        else:
            summary["reaped_exited"] += 1

    conn.commit()
    return summary


# ── events ───────────────────────────────────────────────────────────────────

def _append_event(
    conn: sqlite3.Connection,
    session_id: str,
    event_type: str,
    payload: dict | None = None,
) -> None:
    now = _now_utc()
    conn.execute(
        "INSERT INTO events (session_id, event_type, payload_json, created_at) VALUES (?,?,?,?)",
        (session_id, event_type, json.dumps(payload) if payload else None, now),
    )


def list_events(conn: sqlite3.Connection, session_id: str) -> list[Event]:
    rows = conn.execute(
        "SELECT * FROM events WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return [
        Event(
            id=row["id"],
            session_id=row["session_id"],
            event_type=row["event_type"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
