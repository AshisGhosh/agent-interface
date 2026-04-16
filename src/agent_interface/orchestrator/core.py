"""Core orchestrator operations.

Every mutation appends a row to `task_events` and updates the cached
`tasks.status`. The event log is the source of truth; the column is a
materialized view for fast querying.

All public functions are synchronous, take a `sqlite3.Connection` as first arg
(matching the session registry's style), and commit before returning.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Iterable, Optional

from agent_interface.orchestrator.models import (
    Project,
    Task,
    TaskEvent,
    _now_utc,
)
from agent_interface.orchestrator.states import OPEN_STATUSES, TaskStatus

# ── id helpers ───────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    """Generate a short slug from text for dep references."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def _new_project_id() -> str:
    return "p-" + uuid.uuid4().hex[:8]


def _new_task_id() -> str:
    return "t-" + uuid.uuid4().hex[:8]


# ── row mapping ──────────────────────────────────────────────────────────────

def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        autonomy=row["autonomy"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
    )


def _split_tags(raw: str) -> list[str]:
    return [t for t in (raw or "").split(",") if t]


def _join_tags(tags: Iterable[str]) -> str:
    return ",".join(t.strip() for t in tags if t.strip())


def _row_to_task(conn: sqlite3.Connection, row: sqlite3.Row) -> Task:
    deps = [
        r["depends_on_task_id"]
        for r in conn.execute(
            "SELECT depends_on_task_id FROM task_deps WHERE task_id=?",
            (row["id"],),
        ).fetchall()
    ]
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        status=row["status"],
        description=row["description"],
        priority=row["priority"],
        tags=_split_tags(row["tags"]),
        parent_id=row["parent_id"],
        creator=row["creator"],
        spawned_from_task=row["spawned_from_task"],
        spawned_from_session=row["spawned_from_session"],
        assigned_session_id=row["assigned_session_id"],
        worktree_path=row["worktree_path"],
        depends_on=deps,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
    )


# ── event log ────────────────────────────────────────────────────────────────

def _append_event(
    conn: sqlite3.Connection,
    task_id: str,
    event_type: str,
    *,
    actor: str = "user",
    payload: dict | None = None,
) -> None:
    conn.execute(
        """INSERT INTO task_events (task_id, event_type, actor, payload_json, created_at)
           VALUES (?,?,?,?,?)""",
        (
            task_id,
            event_type,
            actor,
            json.dumps(payload) if payload else None,
            _now_utc(),
        ),
    )


def list_events(conn: sqlite3.Connection, task_id: str) -> list[TaskEvent]:
    rows = conn.execute(
        "SELECT * FROM task_events WHERE task_id=? ORDER BY id ASC",
        (task_id,),
    ).fetchall()
    return [
        TaskEvent(
            id=r["id"],
            task_id=r["task_id"],
            event_type=r["event_type"],
            actor=r["actor"],
            payload_json=r["payload_json"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ── projects ─────────────────────────────────────────────────────────────────

def create_project(
    conn: sqlite3.Connection,
    name: str,
    *,
    description: Optional[str] = None,
    autonomy: str = "none",
) -> Project:
    project = Project(
        id=_new_project_id(),
        name=name,
        description=description,
        autonomy=autonomy,
    )
    conn.execute(
        """INSERT INTO projects
           (id, name, description, autonomy, created_at, updated_at, archived_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            project.id,
            project.name,
            project.description,
            project.autonomy,
            project.created_at,
            project.updated_at,
            project.archived_at,
        ),
    )
    conn.commit()
    return project


def get_project(conn: sqlite3.Connection, id_or_name: str) -> Optional[Project]:
    row = conn.execute(
        "SELECT * FROM projects WHERE id=? OR name=?",
        (id_or_name, id_or_name),
    ).fetchone()
    return _row_to_project(row) if row else None


def list_projects(
    conn: sqlite3.Connection, *, include_archived: bool = False,
) -> list[Project]:
    if include_archived:
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projects WHERE archived_at IS NULL ORDER BY name",
        ).fetchall()
    return [_row_to_project(r) for r in rows]


# ── tasks ────────────────────────────────────────────────────────────────────

def add_task(
    conn: sqlite3.Connection,
    project: str,
    title: str,
    *,
    description: Optional[str] = None,
    priority: int = 2,
    tags: Optional[Iterable[str]] = None,
    parent_id: Optional[str] = None,
    depends_on: Optional[Iterable[str]] = None,
    creator: str = "user",
    spawned_from_task: Optional[str] = None,
    spawned_from_session: Optional[str] = None,
    status: Optional[str] = None,
) -> Task:
    """Create a task.

    User-created tasks default to `ready` (immediately claimable).
    Agent-created tasks default to `backlog` (require triage before claim).
    """
    proj = get_project(conn, project)
    if proj is None:
        raise ValueError(f"No such project: {project}")

    if status is None:
        status = (
            TaskStatus.BACKLOG.value if creator.startswith("session:")
            else TaskStatus.READY.value
        )
    elif hasattr(status, "value"):
        status = status.value

    task = Task(
        id=_new_task_id(),
        project_id=proj.id,
        title=title,
        description=description,
        status=status,
        priority=priority,
        tags=list(tags or []),
        parent_id=parent_id,
        creator=creator,
        spawned_from_task=spawned_from_task,
        spawned_from_session=spawned_from_session,
        depends_on=list(depends_on or []),
    )

    conn.execute(
        """INSERT INTO tasks
           (id, project_id, parent_id, title, description, status, priority, tags,
            creator, spawned_from_task, spawned_from_session,
            assigned_session_id, worktree_path, created_at, updated_at, closed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            task.id, task.project_id, task.parent_id, task.title, task.description,
            task.status, task.priority, _join_tags(task.tags),
            task.creator, task.spawned_from_task, task.spawned_from_session,
            task.assigned_session_id, task.worktree_path,
            task.created_at, task.updated_at, task.closed_at,
        ),
    )
    for dep in task.depends_on:
        conn.execute(
            "INSERT INTO task_deps (task_id, depends_on_task_id) VALUES (?,?)",
            (task.id, dep),
        )

    _append_event(
        conn, task.id, "created",
        actor=creator,
        payload={
            "title": title,
            "priority": priority,
            "status": status,
            "parent_id": parent_id,
            "depends_on": task.depends_on,
        },
    )
    conn.commit()
    return task


def get_task(conn: sqlite3.Connection, task_id: str) -> Optional[Task]:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _row_to_task(conn, row) if row else None


def list_tasks(
    conn: sqlite3.Connection,
    *,
    project: Optional[str] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> list[Task]:
    clauses: list[str] = []
    args: list[object] = []

    if project:
        proj = get_project(conn, project)
        if proj is None:
            return []
        clauses.append("project_id = ?")
        args.append(proj.id)

    if status:
        clauses.append("status = ?")
        args.append(status)
    elif not include_closed:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        clauses.append(f"status IN ({placeholders})")
        args.extend(s.value for s in OPEN_STATUSES)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY priority ASC, created_at ASC",
        args,
    ).fetchall()
    return [_row_to_task(conn, r) for r in rows]


def plan_project(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    task_specs: list[dict],
    *,
    autonomy: str = "none",
    creator: str = "user",
) -> tuple[Project, list[Task]]:
    """Create a project and its full task graph in one transaction.

    Each task_spec is a dict with:
        title (str, required)
        description (str, optional)
        priority (int, optional, default 2)
        tags (list[str], optional)
        depends_on (list[str], optional) — titles (not ids) of other tasks in
            this same plan, resolved by order.

    Returns (project, tasks). All user-created tasks land as `ready` unless
    they have deps (in which case `backlog` until deps are done).
    """
    proj = create_project(conn, name, description=description, autonomy=autonomy)

    title_to_id: dict[str, str] = {}
    tasks: list[Task] = []

    for spec in task_specs:
        title = spec["title"]
        dep_titles = spec.get("depends_on", [])
        dep_ids = [title_to_id[d] for d in dep_titles if d in title_to_id]

        has_deps = bool(dep_ids) or len(dep_ids) < len(dep_titles)
        status = TaskStatus.BACKLOG.value if has_deps else TaskStatus.READY.value

        task = add_task(
            conn,
            proj.id,
            title,
            description=spec.get("description"),
            priority=spec.get("priority", 2),
            tags=spec.get("tags", []),
            depends_on=dep_ids,
            creator=creator,
            status=status,
        )
        title_to_id[title] = task.id
        tasks.append(task)

    return proj, tasks


# ── state transitions ────────────────────────────────────────────────────────

def _set_status(
    conn: sqlite3.Connection,
    task_id: str,
    new_status: str,
    *,
    closed: bool = False,
) -> None:
    now = _now_utc()
    conn.execute(
        "UPDATE tasks SET status=?, updated_at=?, closed_at=? WHERE id=?",
        (new_status, now, now if closed else None, task_id),
    )


def _deps_satisfied(conn: sqlite3.Connection, task_id: str) -> bool:
    rows = conn.execute(
        """SELECT t.status
             FROM task_deps d
             JOIN tasks t ON t.id = d.depends_on_task_id
             WHERE d.task_id=?""",
        (task_id,),
    ).fetchall()
    return all(r["status"] == TaskStatus.DONE for r in rows)


def promote(
    conn: sqlite3.Connection, task_id: str, *, actor: str = "user",
) -> Task:
    """Move a task from backlog → ready (if deps allow)."""
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != TaskStatus.BACKLOG:
        raise ValueError(f"Cannot promote task in status: {task.status}")
    if not _deps_satisfied(conn, task_id):
        raise ValueError("Cannot promote: dependencies not done")

    _set_status(conn, task_id, TaskStatus.READY)
    _append_event(conn, task_id, "ready", actor=actor)
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def claim_next(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    project: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> Optional[Task]:
    """Atomically claim the highest-priority ready task for this session.

    Returns None if nothing is claimable.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        clauses = ["status = ?"]
        args: list[object] = [TaskStatus.READY.value]

        if project:
            proj = get_project(conn, project)
            if proj is None:
                conn.rollback()
                return None
            clauses.append("project_id = ?")
            args.append(proj.id)

        if tags:
            for tag in tags:
                clauses.append(
                    "(',' || tags || ',') LIKE ?"
                )
                args.append(f"%,{tag},%")

        where = " AND ".join(clauses)
        row = conn.execute(
            f"""SELECT * FROM tasks
                WHERE {where}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1""",
            args,
        ).fetchone()

        if row is None:
            conn.rollback()
            return None

        task_id = row["id"]
        if not _deps_satisfied(conn, task_id):
            # Shouldn't happen if we only promote when deps are satisfied, but
            # guard anyway — skip this task for now.
            conn.rollback()
            return None

        now = _now_utc()
        conn.execute(
            """UPDATE tasks
               SET status=?, assigned_session_id=?, updated_at=?
               WHERE id=?""",
            (TaskStatus.IN_PROGRESS.value, session_id, now, task_id),
        )
        _append_event(
            conn, task_id, "claimed",
            actor=f"session:{session_id}",
            payload={"session_id": session_id},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return get_task(conn, task_id)


def progress(
    conn: sqlite3.Connection,
    task_id: str,
    note: str,
    *,
    pct: Optional[int] = None,
    actor: str = "user",
) -> Task:
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    payload: dict = {"note": note}
    if pct is not None:
        payload["pct"] = pct

    _append_event(conn, task_id, "progress", actor=actor, payload=payload)
    conn.execute(
        "UPDATE tasks SET updated_at=? WHERE id=?",
        (_now_utc(), task_id),
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def block_task(
    conn: sqlite3.Connection,
    task_id: str,
    reason: str,
    *,
    needs: str = "user",
    actor: str = "user",
) -> Task:
    if needs not in ("user", "dep", "resource"):
        raise ValueError(f"Invalid needs: {needs}")

    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    _set_status(conn, task_id, TaskStatus.BLOCKED)
    _append_event(
        conn, task_id, "blocked",
        actor=actor,
        payload={"reason": reason, "needs": needs},
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def unblock_task(
    conn: sqlite3.Connection, task_id: str, *, actor: str = "user",
) -> Task:
    """Move a blocked task back to in_progress (if it has an assignee) or ready."""
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != TaskStatus.BLOCKED:
        raise ValueError(f"Task not blocked: {task.status}")

    new_status = (
        TaskStatus.IN_PROGRESS if task.assigned_session_id else TaskStatus.READY
    )
    _set_status(conn, task_id, new_status)
    _append_event(conn, task_id, "unblocked", actor=actor)
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def done_task(
    conn: sqlite3.Connection,
    task_id: str,
    summary: str,
    *,
    spawned: Optional[Iterable[str]] = None,
    actor: str = "user",
) -> Task:
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    _set_status(conn, task_id, TaskStatus.DONE, closed=True)
    _append_event(
        conn, task_id, "done",
        actor=actor,
        payload={"summary": summary, "spawned": list(spawned or [])},
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def reopen_task(
    conn: sqlite3.Connection, task_id: str, *, actor: str = "user",
) -> Task:
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != TaskStatus.DONE:
        raise ValueError(f"Can only reopen done tasks, got: {task.status}")

    now = _now_utc()
    conn.execute(
        """UPDATE tasks SET status=?, closed_at=NULL, updated_at=? WHERE id=?""",
        (TaskStatus.IN_PROGRESS.value if task.assigned_session_id
         else TaskStatus.READY.value, now, task_id),
    )
    _append_event(conn, task_id, "reopened", actor=actor)
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]
