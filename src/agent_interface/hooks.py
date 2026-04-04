"""Hook processing and installation for coding agent session lifecycle."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Optional

from agent_interface.db import get_connection
from agent_interface.models import Session, _now_utc
from agent_interface.registry import (
    _row_to_session,
    get_session,
    register_session,
    update_state,
)
from agent_interface.states import SessionState

# Hook events we care about and the state they map to.
EVENT_STATE_MAP: dict[str, str] = {
    "SessionStart": SessionState.RUNNING,
    "Stop": SessionState.WAITING_FOR_USER,
    "Notification": SessionState.WAITING_FOR_USER,
    "PostToolUse": SessionState.RUNNING,
    "SessionEnd": SessionState.DONE,
}

# PostToolUse fires very frequently — only update last_seen_at, skip state
# change if already running.
HEARTBEAT_ONLY_EVENTS = {"PostToolUse"}

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

HOOK_EVENTS = ["SessionStart", "Stop", "PostToolUse", "Notification", "SessionEnd"]


def _get_parent_pid(pid: int) -> Optional[int]:
    """Get parent PID from /proc."""
    try:
        from pathlib import Path

        stat = Path(f"/proc/{pid}/stat").read_text()
        close_paren = stat.rfind(")")
        fields = stat[close_paren + 2:].split()
        return int(fields[1])
    except (OSError, IndexError, ValueError):
        return None


def _find_by_pid_ancestry(conn: Any, pid: int) -> Optional[Session]:
    """Walk up the process tree to find a scan-registered session."""
    # Collect all active PIDs in one query.
    rows = conn.execute(
        "SELECT pid FROM sessions WHERE pid IS NOT NULL AND state NOT IN ('done','archived')",
    ).fetchall()
    known_pids = {row["pid"] for row in rows}

    visited: set[int] = set()
    current: Optional[int] = pid
    while current and current > 1 and current not in visited:
        visited.add(current)
        if current in known_pids:
            row = conn.execute(
                "SELECT * FROM sessions WHERE pid=? AND state NOT IN ('done','archived') LIMIT 1",
                (current,),
            ).fetchone()
            if row:
                return _row_to_session(row)
        current = _get_parent_pid(current)
    return None


def _build_hook_entry() -> dict:
    """Build a single hook entry pointing to `agi hook`."""
    return {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": "agi hook",
            }
        ],
    }


def generate_hook_config() -> dict[str, list[dict]]:
    """Generate the hooks config block for settings.json."""
    return {event: [_build_hook_entry()] for event in HOOK_EVENTS}


def install_hooks() -> tuple[bool, str]:
    """Write hook config into ~/.claude/settings.json.

    Merges with existing settings. Returns (success, message).
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    settings: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}

    existing_hooks = settings.get("hooks", {})
    new_hooks = generate_hook_config()

    # Check for conflicts — warn if hooks already exist for these events.
    conflicts = [e for e in HOOK_EVENTS if e in existing_hooks]

    # Merge: our hooks replace entries for our events, preserve others.
    existing_hooks.update(new_hooks)
    settings["hooks"] = existing_hooks

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")

    if conflicts:
        return True, f"Hooks installed. Replaced existing hooks for: {', '.join(conflicts)}"
    return True, "Hooks installed."


def process_hook(payload: dict) -> str:
    """Process a hook event payload from stdin.

    Returns a short status message.
    """
    event_name = payload.get("hook_event_name", "")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not session_id:
        return "ignored: no session_id"

    target_state = EVENT_STATE_MAP.get(event_name)
    if not target_state:
        return f"ignored: unknown event {event_name}"

    conn = get_connection()
    pid = os.getppid()

    # Try to adopt a scan-registered session by walking the process tree.
    # This must happen before checking by session_id so scan entries get merged.
    scan_match = _find_by_pid_ancestry(conn, pid)
    if scan_match and scan_match.id != session_id:
        now = _now_utc()
        # Merge: delete any existing entry for this session_id (from a prior hook),
        # then rename the scan entry to use the hook's session_id.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.execute(
            "UPDATE sessions SET id=?, state=?, cwd=?, updated_at=?, last_seen_at=? WHERE id=?",
            (session_id, target_state.value, cwd or scan_match.cwd, now, now, scan_match.id),
        )
        conn.execute(
            "UPDATE events SET session_id=? WHERE session_id=?",
            (session_id, scan_match.id),
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        return f"adopted: {scan_match.id} → {session_id} ({target_state.value})"

    existing = get_session(conn, session_id)

    if existing is None:
        # No scan match, no existing entry — register new session.
        session = Session(
            id=session_id,
            state=target_state.value,
            host=socket.gethostname(),
            cwd=cwd,
            pid=pid,
        )
        register_session(conn, session)
        return f"registered: {session_id} ({target_state.value})"

    # For heartbeat-only events, just touch last_seen_at if already running.
    if event_name in HEARTBEAT_ONLY_EVENTS and existing.state == SessionState.RUNNING.value:
        now = _now_utc()
        conn.execute(
            "UPDATE sessions SET last_seen_at=?, updated_at=? WHERE id=?",
            (now, now, session_id),
        )
        conn.commit()
        return f"heartbeat: {session_id}"

    # Update state.
    update_state(conn, session_id, target_state.value)
    return f"updated: {session_id} → {target_state.value}"


def read_and_process_stdin() -> str:
    """Read hook JSON from stdin and process it."""
    raw = sys.stdin.read()
    if not raw.strip():
        return "ignored: empty stdin"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "error: invalid JSON on stdin"
    return process_hook(payload)
