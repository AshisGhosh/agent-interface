"""MCP server exposing orchestrator operations as typed tools.

This is the primary agent-facing interface. Users still interact via the CLI;
agents running inside Claude Code (or any MCP client) see these tools in their
tool list with schema-validated arguments.

Run via `agi mcp`. Configured in a Claude Code settings file as:

    {"mcpServers": {"agi": {"command": "agi", "args": ["mcp"]}}}

Session binding: every tool resolves the calling session via
  1. AGI_SESSION_ID env (set by `agi dispatch` for ephemeral sessions), or
  2. PID ancestry (server's parent tree contains the Claude process, which
     is registered in the session registry).

If neither works, tools that require a session id will raise a clear error.
"""

from __future__ import annotations

import os
from typing import Optional

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import get_connection


def _current_session_id() -> Optional[str]:
    env_id = os.environ.get("AGI_SESSION_ID")
    if env_id:
        return env_id
    try:
        from agent_interface.db import get_connection as _base_conn
        from agent_interface.hooks import _find_by_pid_ancestry

        match = _find_by_pid_ancestry(_base_conn(), os.getpid())
        return match.id if match else None
    except Exception:
        return None


def _require_session() -> str:
    sid = _current_session_id()
    if sid is None:
        raise RuntimeError(
            "Could not resolve current session. Set AGI_SESSION_ID or run "
            "this MCP server from inside a tracked Claude Code session."
        )
    return sid


def _task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "tags": task.tags,
        "description": task.description,
        "parent_id": task.parent_id,
        "creator": task.creator,
        "assigned_session_id": task.assigned_session_id,
        "depends_on": task.depends_on,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def build_server():
    """Construct the FastMCP server. Imported lazily so `mcp` is optional."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("agi")

    @server.tool()
    def get_assignment() -> dict:
        """Return the task currently assigned to this session, if any.

        Call this at the start of a session to pick up where you left off.
        Returns {"task": null} if no task is assigned.
        """
        sid = _current_session_id()
        if sid is None:
            return {"task": None, "session_id": None}

        conn = get_connection()
        from agent_interface.orchestrator.hooks import _find_assigned_task

        task = _find_assigned_task(conn, sid)
        return {
            "session_id": sid,
            "task": _task_to_dict(task) if task else None,
        }

    @server.tool()
    def claim_next(
        project: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Atomically claim the highest-priority ready task for this session.

        Args:
            project: Limit to tasks in this project (id or name).
            tags: Only consider tasks that have *all* listed tags.

        Returns the claimed task, or {"task": null} if nothing was available.
        Respects dependencies — tasks whose deps aren't done are never claimed.
        """
        sid = _require_session()
        conn = get_connection()
        task = core.claim_next(conn, sid, project=project, tags=tags)
        return {"task": _task_to_dict(task) if task else None}

    @server.tool()
    def progress(task_id: str, note: str, pct: Optional[int] = None) -> dict:
        """Report progress on a task. Appends to the event log.

        Args:
            task_id: Task id.
            note: Short human-readable note.
            pct: Optional completion percentage (0-100).
        """
        sid = _current_session_id()
        actor = f"session:{sid}" if sid else "system"
        conn = get_connection()
        task = core.progress(conn, task_id, note, pct=pct, actor=actor)
        return _task_to_dict(task)

    @server.tool()
    def block(task_id: str, reason: str, needs: str = "user") -> dict:
        """Mark a task as blocked.

        Args:
            task_id: Task id.
            reason: Why it's blocked (shown to the user).
            needs: One of 'user' (human decision), 'dep' (another task),
                'resource' (e.g. credentials, GPU).
        """
        sid = _current_session_id()
        actor = f"session:{sid}" if sid else "system"
        conn = get_connection()
        task = core.block_task(conn, task_id, reason, needs=needs, actor=actor)
        return _task_to_dict(task)

    @server.tool()
    def done(
        task_id: str,
        summary: str,
        spawned: Optional[list[str]] = None,
    ) -> dict:
        """Mark a task complete.

        Args:
            task_id: Task id.
            summary: What was done and the outcome (1-3 sentences).
            spawned: Optional task ids created as follow-ups to this one.
        """
        sid = _current_session_id()
        actor = f"session:{sid}" if sid else "system"
        conn = get_connection()
        task = core.done_task(conn, task_id, summary, spawned=spawned, actor=actor)
        return _task_to_dict(task)

    @server.tool()
    def add_subtask(
        parent_id: str,
        title: str,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Create a subtask under an existing task.

        Subtasks land in backlog and require user promotion before being
        claimable. Use this when you discover work that should be tracked
        separately from the current task.
        """
        sid = _require_session()
        conn = get_connection()
        parent = core.get_task(conn, parent_id)
        if parent is None:
            raise ValueError(f"No such parent task: {parent_id}")

        task = core.add_task(
            conn,
            parent.project_id,
            title,
            description=description,
            priority=priority if priority is not None else parent.priority,
            tags=tags or [],
            parent_id=parent_id,
            creator=f"session:{sid}",
            spawned_from_task=parent_id,
            spawned_from_session=sid,
        )
        return _task_to_dict(task)

    @server.tool()
    def add_task(
        project: str,
        title: str,
        description: Optional[str] = None,
        priority: int = 2,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Create a peer task in a project.

        Peer tasks land in backlog and require user triage (`agi triage`)
        before being claimable. Prefer add_subtask when the new work is a
        child of your current task.
        """
        sid = _require_session()
        conn = get_connection()
        task = core.add_task(
            conn, project, title,
            description=description,
            priority=priority,
            tags=tags or [],
            creator=f"session:{sid}",
            spawned_from_session=sid,
        )
        return _task_to_dict(task)

    @server.tool()
    def get_task(task_id: str) -> dict:
        """Fetch full details for a task, including recent events."""
        conn = get_connection()
        task = core.get_task(conn, task_id)
        if task is None:
            raise ValueError(f"No such task: {task_id}")
        events = core.list_events(conn, task_id)
        return {
            **_task_to_dict(task),
            "events": [
                {
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "payload": e.payload_json,
                    "created_at": e.created_at,
                }
                for e in events[-20:]  # last 20 for context
            ],
        }

    @server.tool()
    def list_my_tasks() -> dict:
        """List open tasks assigned to the current session."""
        sid = _require_session()
        conn = get_connection()
        rows = conn.execute(
            """SELECT id FROM tasks
               WHERE assigned_session_id=?
               AND status NOT IN ('done')
               ORDER BY updated_at DESC""",
            (sid,),
        ).fetchall()
        tasks = [core.get_task(conn, r["id"]) for r in rows]
        return {"tasks": [_task_to_dict(t) for t in tasks if t]}

    return server


def run() -> None:
    """Entry point for `agi mcp` — runs the server on stdio transport."""
    server = build_server()
    server.run()
