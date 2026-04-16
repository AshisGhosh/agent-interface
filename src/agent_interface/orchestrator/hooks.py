"""Orchestrator hook handlers.

Called from `agent_interface.hooks.process_hook` on SessionStart / SessionEnd
to keep task state in sync with session lifecycle events. All functions here
are best-effort and must never raise — the session hook pipeline must not be
impacted by orchestrator problems.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import get_connection
from agent_interface.orchestrator.states import TaskStatus


def _find_assigned_task(conn, session_id: str):
    """Return a task currently assigned to this session, if any."""
    open_statuses = [
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.REVIEW.value,
        TaskStatus.BLOCKED.value,
    ]
    placeholders = ",".join("?" for _ in open_statuses)
    row = conn.execute(
        f"""SELECT id FROM tasks
           WHERE assigned_session_id=? AND status IN ({placeholders})
           ORDER BY updated_at DESC LIMIT 1""",
        (session_id, *open_statuses),
    ).fetchone()
    return core.get_task(conn, row["id"]) if row else None


def _find_task_by_env() -> Optional[str]:
    """Return AGI_TASK_ID if set."""
    tid = os.environ.get("AGI_TASK_ID")
    return tid if tid else None


def on_session_start(session_id: str, cwd: Optional[str]) -> Optional[dict]:
    """Build an assignment-context payload for Claude Code SessionStart.

    Returns a dict matching Claude Code's hook additionalContext schema, or
    None if there's nothing to inject. Callers should json.dumps and print.
    """
    try:
        conn = get_connection()

        # Preferred: explicit env binding from dispatch.
        env_task_id = _find_task_by_env()
        task = None
        if env_task_id:
            task = core.get_task(conn, env_task_id)
            if task and task.assigned_session_id != session_id:
                # Bind now (dispatched session is seeing its task for the first time).
                conn.execute(
                    "UPDATE tasks SET assigned_session_id=?, updated_at=? WHERE id=?",
                    (session_id, task.created_at, task.id),
                )
                # Also move to in_progress if still ready.
                if task.status == TaskStatus.READY.value:
                    conn.execute(
                        "UPDATE tasks SET status=? WHERE id=?",
                        (TaskStatus.IN_PROGRESS.value, task.id),
                    )
                conn.execute(
                    "INSERT INTO task_events"
                    " (task_id, event_type, actor, payload_json, created_at)"
                    " VALUES (?,?,?,?,datetime('now'))",
                    (
                        task.id, "assigned", f"session:{session_id}",
                        json.dumps({"session_id": session_id, "source": "env"}),
                    ),
                )
                conn.commit()
                task = core.get_task(conn, task.id)

        # Fallback: session already bound from a prior claim.
        if task is None:
            task = _find_assigned_task(conn, session_id)

        if task is None:
            return None

        context = _format_assignment(task)
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    except Exception:
        return None


def on_session_end(session_id: str) -> None:
    """Auto-block any open tasks assigned to a session that's ending.

    Prevents orphaned in-progress work from silently disappearing.
    """
    try:
        conn = get_connection()
        task = _find_assigned_task(conn, session_id)
        if task is None:
            return
        if task.status == TaskStatus.BLOCKED.value:
            return  # already blocked
        core.block_task(
            conn, task.id,
            reason="session_ended_without_resolution",
            needs="user",
            actor="system",
        )
    except Exception:
        pass


def _format_assignment(task) -> str:
    """Format an assignment notice for Claude Code SessionStart injection."""
    lines = [
        "## Your assignment",
        "",
        f"You are working on task **{task.id}**: {task.title}",
        "",
        f"- Project: {task.project_id}",
        f"- Priority: p{task.priority}",
        f"- Status: {task.status}",
    ]
    if task.tags:
        lines.append(f"- Tags: {', '.join(task.tags)}")
    if task.depends_on:
        lines.append(f"- Dependencies: {', '.join(task.depends_on)}")
    if task.description:
        lines.extend(["", "### Description", "", task.description])
    lines.extend([
        "",
        "Use the `agi` MCP tools to report progress (`progress`), surface "
        "blockers (`block`), spawn follow-up work (`add_subtask`), and mark "
        "the task complete (`done`). Do not silently end the session while "
        "this task is still open — call `done` or `block` first.",
    ])
    return "\n".join(lines)
