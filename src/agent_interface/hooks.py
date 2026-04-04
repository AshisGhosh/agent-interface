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
    rename_session,
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
CLAUDE_MD_PATH = Path.home() / ".claude" / "CLAUDE.md"

AGI_INSTRUCTION_MARKER = "<!-- agi:auto-label -->"
AGI_INSTRUCTION = f"""{AGI_INSTRUCTION_MARKER}
## Session labeling (agi)

This machine uses `agi` to track coding agent sessions. When you start working on a task, \
update your session label by running:
  agi label "5-10 word summary of the task"

Keep the summary short and descriptive. Update it if the focus of work changes significantly.
Do not update the label on every prompt — only when the task itself changes.
"""

HOOK_EVENTS = [
    "SessionStart", "Stop", "PostToolUse", "Notification", "SessionEnd",
    "UserPromptSubmit",
]

MAX_LABEL_LENGTH = 60


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


def _install_claude_md() -> str:
    """Append agi instruction to ~/.claude/CLAUDE.md if not already present."""
    CLAUDE_MD_PATH.parent.mkdir(parents=True, exist_ok=True)

    if CLAUDE_MD_PATH.exists():
        content = CLAUDE_MD_PATH.read_text()
        if AGI_INSTRUCTION_MARKER in content:
            # Replace existing instruction with latest version.
            import re

            pattern = re.escape(AGI_INSTRUCTION_MARKER) + r".*?(?=\n<!-- |$)"
            content = re.sub(pattern, AGI_INSTRUCTION.rstrip(), content, flags=re.DOTALL)
            CLAUDE_MD_PATH.write_text(content)
            return "CLAUDE.md instruction updated."
    else:
        content = ""

    content = content.rstrip() + "\n\n" + AGI_INSTRUCTION if content else AGI_INSTRUCTION
    CLAUDE_MD_PATH.write_text(content)
    return "CLAUDE.md instruction added."


def install_hooks() -> tuple[bool, str]:
    """Write hook config into ~/.claude/settings.json and CLAUDE.md instruction.

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

    conflicts = [e for e in HOOK_EVENTS if e in existing_hooks]

    existing_hooks.update(new_hooks)
    settings["hooks"] = existing_hooks

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")

    claude_md_msg = _install_claude_md()

    parts = ["Hooks installed."]
    if conflicts:
        parts.append(f"Replaced hooks for: {', '.join(conflicts)}.")
    parts.append(claude_md_msg)
    return True, " ".join(parts)


def _truncate_label(text: str) -> str:
    """Truncate to MAX_LABEL_LENGTH, breaking at word boundary."""
    text = text.strip().split("\n")[0]  # First line only.
    if len(text) <= MAX_LABEL_LENGTH:
        return text
    truncated = text[:MAX_LABEL_LENGTH].rsplit(" ", 1)[0]
    return truncated


def _handle_prompt_label(session_id: str, prompt: str) -> str:
    """Set state to running and label from first user prompt."""
    conn = get_connection()
    existing = get_session(conn, session_id)
    if existing is None:
        return "ignored: session not found"

    # User just submitted input — session is now running.
    if existing.state != SessionState.RUNNING.value:
        update_state(conn, session_id, SessionState.RUNNING.value)

    if not prompt.strip():
        return "running: empty prompt"

    # Don't overwrite an existing label.
    if existing.label:
        return f"running: label already set for {session_id}"

    label = _truncate_label(prompt)
    rename_session(conn, session_id, label)
    return f"labeled: {session_id} → {label}"


_LAST_NOTIFY_PATH = Path.home() / ".config" / "agi" / "last_notify.json"


def _try_notify(session_id: str, transcript_path: str | None) -> None:
    """Send a Telegram notification if configured. Best-effort, never fails."""
    try:
        import time

        # Small delay to let the transcript flush to disk.
        time.sleep(1)

        from agent_interface.telegram import _read_last_agent_message, notify_waiting

        last_msg = None
        if transcript_path:
            last_msg = _read_last_agent_message(transcript_path)

        # Dedup by content: skip if we'd send the exact same message again.
        msg_key = f"{session_id}:{(last_msg or '')[-200:]}"
        last_sent: dict[str, str] = {}
        if _LAST_NOTIFY_PATH.exists():
            try:
                last_sent = json.loads(_LAST_NOTIFY_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if last_sent.get(session_id) == msg_key:
            return

        if notify_waiting(session_id, last_msg):
            last_sent[session_id] = msg_key
            _LAST_NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LAST_NOTIFY_PATH.write_text(json.dumps(last_sent))
    except Exception:
        pass  # Notifications are best-effort.


def _try_update_dashboard() -> None:
    """Update the pinned dashboard. Best-effort."""
    try:
        from agent_interface.telegram import update_dashboard
        update_dashboard()
    except Exception:
        pass


def process_hook(payload: dict) -> str:
    """Process a hook event payload from stdin.

    Returns a short status message.
    """
    event_name = payload.get("hook_event_name", "")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not session_id:
        return "ignored: no session_id"

    # Handle UserPromptSubmit — auto-label from first prompt.
    if event_name == "UserPromptSubmit":
        return _handle_prompt_label(session_id, payload.get("prompt", ""))

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
        if event_name == "Stop":
            _try_notify(session_id, payload.get("transcript_path"))
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
        if event_name == "Stop":
            _try_notify(session_id, payload.get("transcript_path"))
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

    # Notify on Stop — the agent just finished a response.
    # Deduplicate: skip if we already notified for this session recently.
    if event_name == "Stop":
        _try_notify(session_id, payload.get("transcript_path"))

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
    result = process_hook(payload)
    _try_update_dashboard()
    return result
