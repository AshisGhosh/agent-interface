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

    has_deps = bool(depends_on)
    if status is None:
        if creator.startswith("session:") or has_deps:
            status = TaskStatus.BACKLOG.value
        else:
            status = TaskStatus.READY.value
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
    reap: bool = True,
) -> list[Task]:
    if reap:
        try:
            reap_orphaned_tasks(conn)
        except sqlite3.OperationalError:
            # Lock contention with other processes — skip reap this call.
            # Not catastrophic; reap runs on every list so next call retries.
            pass

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
    # Close the dispatched session — the agent is gone, no point showing it
    # as running. User can redispatch (new placeholder) after unblocking.
    if task.assigned_session_id:
        conn.execute(
            """UPDATE sessions SET state='done', updated_at=?
               WHERE id=? AND is_managed=1""",
            (_now_utc(), task.assigned_session_id),
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
    """Close a task. If the task has a worktree, auto-commit first.

    Outcomes:
    - worktree clean (nothing to commit) → DONE
    - commit succeeds → DONE
    - commit fails (pre-commit hook, conflict, etc.) → REVIEW with error
      payload so the user can inspect and decide

    Also closes the assigned session (if any) so `agi list` stops showing
    dispatched agents as running after their task is complete.
    """
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    commit_result = None
    if task.worktree_path:
        commit_result = _commit_worktree(task, summary)

    if commit_result and commit_result["status"] == "failed":
        # Move to review instead of done so the user can triage.
        _set_status(conn, task_id, TaskStatus.REVIEW)
        _append_event(
            conn, task_id, "review_requested",
            actor=actor,
            payload={
                "summary": summary,
                "reason": "commit_failed",
                "error": commit_result.get("error", ""),
                "spawned": list(spawned or []),
            },
        )
        conn.commit()
        return get_task(conn, task_id)  # type: ignore[return-value]

    _set_status(conn, task_id, TaskStatus.DONE, closed=True)
    done_payload = {"summary": summary, "spawned": list(spawned or [])}
    if commit_result:
        done_payload["commit"] = commit_result
    _append_event(
        conn, task_id, "done",
        actor=actor,
        payload=done_payload,
    )
    # Mark the dispatched placeholder session done so it drops off `agi list`.
    # We only close managed sessions — never mess with ad-hoc user sessions.
    if task.assigned_session_id:
        conn.execute(
            """UPDATE sessions SET state='done', updated_at=?
               WHERE id=? AND is_managed=1""",
            (_now_utc(), task.assigned_session_id),
        )
    conn.commit()

    # Auto-promote dependents whose deps are now all satisfied.
    _auto_promote_dependents(conn, task_id)

    return get_task(conn, task_id)  # type: ignore[return-value]


def _commit_worktree(task: Task, summary: str) -> dict:
    """Commit uncommitted changes in a task's worktree.

    Returns a dict with keys:
      status: 'clean' (no changes) | 'committed' | 'failed'
      sha: commit hash (on committed)
      error: stderr (on failed)
      files: int — files changed (on committed)
    """
    import subprocess as _sub

    wt = task.worktree_path
    if not wt:
        return {"status": "clean"}

    # Any uncommitted changes (staged or unstaged, including new files).
    porcelain = _sub.run(
        ["git", "status", "--porcelain"],
        cwd=wt, capture_output=True, text=True, timeout=15,
    )
    if porcelain.returncode != 0:
        return {"status": "failed", "error": porcelain.stderr.strip()}
    if not porcelain.stdout.strip():
        return {"status": "clean"}

    # Stage everything.
    add = _sub.run(
        ["git", "add", "-A"],
        cwd=wt, capture_output=True, text=True, timeout=30,
    )
    if add.returncode != 0:
        return {"status": "failed", "error": add.stderr.strip()}

    # Commit. Let precommit hooks run; if they fail, surface the error.
    # Shorten summary to a title + body.
    first_line = summary.strip().split("\n", 1)[0][:72]
    body = summary.strip()
    if first_line:
        msg = f"{task.title}\n\n{body}\n\nTask: {task.id}"
    else:
        msg = f"{task.title}\n\nTask: {task.id}"

    commit = _sub.run(
        ["git", "commit", "-m", msg],
        cwd=wt, capture_output=True, text=True, timeout=120,
    )
    if commit.returncode != 0:
        # Could be pre-commit hook failure, signing failure, etc.
        err = (commit.stderr + commit.stdout).strip()[-2000:]
        return {"status": "failed", "error": err}

    # Grab the new sha.
    rev = _sub.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt, capture_output=True, text=True, timeout=10,
    )
    sha = rev.stdout.strip()[:12] if rev.returncode == 0 else None

    files_changed = len([ln for ln in porcelain.stdout.splitlines() if ln.strip()])
    result = {"status": "committed", "sha": sha, "files": files_changed}

    # After the task-branch commit, rebase onto main and squash-merge into
    # main. Keeps main linear (one commit per task) and makes subsequent
    # dispatches see prior work automatically.
    merge = _rebase_and_squash_to_main(task, summary)
    if merge.get("status") == "failed":
        # Return the whole thing as failed so the caller moves task to review.
        return {
            "status": "failed",
            "error": merge.get("error", "merge failed"),
            "task_branch_sha": sha,
            "files": files_changed,
        }
    if merge.get("status") == "merged":
        result["main_sha"] = merge.get("sha")
    return result


def _rebase_and_squash_to_main(task: "Task", summary: str) -> dict:
    """Rebase the task branch onto main, then squash-merge into main.

    Returns {status: 'skipped'|'merged'|'failed', sha?, error?}. Skipped when
    the repo has no main-branch worktree we can merge into.
    """
    import fcntl as _fc
    import subprocess as _sub

    wt = task.worktree_path
    if not wt:
        return {"status": "skipped", "reason": "no worktree"}

    # Only do the squash-to-main dance when this worktree is on a task/*
    # branch. If the agent committed directly to main (e.g. tests, or
    # dispatch couldn't create a worktree), there's nothing to merge.
    branch_q = _sub.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wt, capture_output=True, text=True, timeout=10,
    )
    current_branch = branch_q.stdout.strip() if branch_q.returncode == 0 else ""
    expected_branch = f"task/{task.id}"
    if current_branch != expected_branch:
        return {
            "status": "skipped",
            "reason": f"worktree on {current_branch!r}, not {expected_branch!r}",
        }

    # Find the main-branch worktree in the same repo.
    wt_list = _sub.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=wt, capture_output=True, text=True, timeout=10,
    )
    if wt_list.returncode != 0:
        return {"status": "failed", "error": wt_list.stderr.strip()}

    main_wt = None
    current_path = None
    for ln in wt_list.stdout.splitlines():
        if ln.startswith("worktree "):
            current_path = ln.split(" ", 1)[1]
        elif ln.strip() == "branch refs/heads/main" and current_path:
            main_wt = current_path
            break
    if not main_wt:
        return {"status": "skipped", "reason": "no main worktree"}

    branch = expected_branch

    # Rebase task branch onto main.
    rebase = _sub.run(
        ["git", "rebase", "main"],
        cwd=wt, capture_output=True, text=True, timeout=120,
    )
    if rebase.returncode != 0:
        _sub.run(
            ["git", "rebase", "--abort"],
            cwd=wt, capture_output=True, timeout=30,
        )
        return {
            "status": "failed",
            "error": "rebase conflict: " + (rebase.stderr or rebase.stdout).strip()[-1500:],
        }

    # Serialize merges so parallel dones don't clobber main.
    import os as _os
    lock_path = _os.path.join(main_wt, ".git", "agi-merge.lock")
    _os.makedirs(_os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lf:
        _fc.flock(lf.fileno(), _fc.LOCK_EX)
        try:
            # Refuse to proceed if main has uncommitted changes — we'd
            # mingle user work with the squash commit.
            dirty = _sub.run(
                ["git", "status", "--porcelain"],
                cwd=main_wt, capture_output=True, text=True, timeout=10,
            )
            if dirty.stdout.strip():
                return {
                    "status": "failed",
                    "error": f"main worktree has uncommitted changes: {dirty.stdout.strip()[:300]}",
                }
            # Squash merge.
            squash = _sub.run(
                ["git", "merge", "--squash", branch],
                cwd=main_wt, capture_output=True, text=True, timeout=120,
            )
            if squash.returncode != 0:
                _sub.run(
                    ["git", "merge", "--abort"],
                    cwd=main_wt, capture_output=True, timeout=30,
                )
                err = (squash.stderr or squash.stdout).strip()[-1500:]
                return {
                    "status": "failed",
                    "error": f"squash merge failed: {err}",
                }
            # Commit the squash result.
            msg = (
                f"{task.title}\n\n"
                f"{summary.strip()}\n\n"
                f"Task: {task.id}"
            )
            commit = _sub.run(
                ["git", "commit", "-m", msg],
                cwd=main_wt, capture_output=True, text=True, timeout=120,
            )
            if commit.returncode != 0:
                _sub.run(
                    ["git", "reset", "--hard", "HEAD"],
                    cwd=main_wt, capture_output=True, timeout=30,
                )
                err = (commit.stderr or commit.stdout).strip()[-1500:]
                return {
                    "status": "failed",
                    "error": f"commit on main failed: {err}",
                }
            rev = _sub.run(
                ["git", "rev-parse", "HEAD"],
                cwd=main_wt, capture_output=True, text=True, timeout=10,
            )
            sha = rev.stdout.strip()[:12] if rev.returncode == 0 else None
            return {"status": "merged", "sha": sha}
        finally:
            _fc.flock(lf.fileno(), _fc.LOCK_UN)


def approve_review(
    conn: sqlite3.Connection, task_id: str, *, actor: str = "user",
) -> Task:
    """Approve a task in review → DONE."""
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != TaskStatus.REVIEW.value:
        raise ValueError(f"Task not in review: {task.status}")

    _set_status(conn, task_id, TaskStatus.DONE, closed=True)
    _append_event(conn, task_id, "approved", actor=actor)
    conn.commit()
    _auto_promote_dependents(conn, task_id)
    return get_task(conn, task_id)  # type: ignore[return-value]


def reject_review(
    conn: sqlite3.Connection, task_id: str, reason: str, *, actor: str = "user",
) -> Task:
    """Reject a task in review → back to in_progress (or ready if unassigned)."""
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != TaskStatus.REVIEW.value:
        raise ValueError(f"Task not in review: {task.status}")

    new_status = (
        TaskStatus.IN_PROGRESS.value if task.assigned_session_id
        else TaskStatus.READY.value
    )
    _set_status(conn, task_id, new_status)
    _append_event(
        conn, task_id, "rejected", actor=actor, payload={"reason": reason},
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def _auto_promote_dependents(conn: sqlite3.Connection, completed_task_id: str) -> list[str]:
    """Promote backlog tasks to ready when all their deps are done.

    Only auto-promotes user-created tasks — agent-created tasks stay in
    backlog so they flow through triage (keeps the 'creation cheap,
    execution gated' invariant).

    Returns ids of tasks that were promoted.
    """
    dependents = conn.execute(
        """SELECT t.id FROM tasks t
           JOIN task_deps d ON d.task_id = t.id
           WHERE d.depends_on_task_id=?
             AND t.status=?
             AND t.creator='user'""",
        (completed_task_id, TaskStatus.BACKLOG.value),
    ).fetchall()

    promoted: list[str] = []
    for row in dependents:
        tid = row["id"]
        if _deps_satisfied(conn, tid):
            _set_status(conn, tid, TaskStatus.READY)
            _append_event(
                conn, tid, "ready",
                actor="system",
                payload={"reason": "deps_satisfied", "trigger": completed_task_id},
            )
            promoted.append(tid)
    if promoted:
        conn.commit()
    return promoted


def reap_orphaned_tasks(conn: sqlite3.Connection) -> list[str]:
    """Reset tasks whose assigned session is dead back to ready.

    A task is considered orphaned when:
    - status is in_progress or blocked
    - assigned_session_id points to a session that is done/archived, missing,
      or has a pid that no longer exists.

    The task goes back to ready with a 'session_orphaned' event. The worktree
    path is preserved so a re-dispatch can reuse it. Returns the ids of
    tasks that were reaped.
    """

    rows = conn.execute(
        """SELECT t.id, t.assigned_session_id, t.status,
                  s.state as sess_state, s.pid as sess_pid
             FROM tasks t
             LEFT JOIN sessions s ON s.id = t.assigned_session_id
             WHERE t.assigned_session_id IS NOT NULL
               AND t.status IN (?, ?)""",
        (TaskStatus.IN_PROGRESS.value, TaskStatus.BLOCKED.value),
    ).fetchall()

    def _dead(row) -> bool:
        """A session is 'dead' only when we have definitive evidence.

        Pid-based checks are unreliable in practice — Claude Code may spawn
        short-lived sub-processes per tool/call, and their pids exit quickly
        while the main agent continues. So we only trust explicit state
        signals: state=done/archived (set by the SessionEnd hook) or the
        session row missing entirely.
        """
        if row["sess_state"] is None:
            return True
        return row["sess_state"] in ("done", "archived")

    reaped: list[str] = []
    for row in rows:
        if _dead(row):
            now = _now_utc()
            conn.execute(
                """UPDATE tasks
                   SET status=?, assigned_session_id=NULL, updated_at=?
                   WHERE id=?""",
                (TaskStatus.READY.value, now, row["id"]),
            )
            _append_event(
                conn, row["id"], "session_orphaned",
                actor="system",
                payload={
                    "previous_session": row["assigned_session_id"],
                    "previous_status": row["status"],
                },
            )
            reaped.append(row["id"])

    if reaped:
        conn.commit()
    return reaped


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


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    """Hard-delete a task and all its related rows.

    Refuses to delete a task that is a parent of other tasks, so callers
    can't orphan children silently.
    """
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    children = conn.execute(
        "SELECT id FROM tasks WHERE parent_id=?", (task_id,),
    ).fetchall()
    if children:
        raise ValueError(
            f"Cannot delete task with {len(children)} child task(s): {task_id}",
        )

    conn.execute("DELETE FROM task_deps WHERE task_id=? OR depends_on_task_id=?",
                 (task_id, task_id))
    conn.execute("DELETE FROM task_events WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM task_notes WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()


def update_task_fields(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    priority: Optional[int] = None,
    assigned_session_id: Optional[str] = None,
    clear_assignment: bool = False,
    actor: str = "user",
) -> Task:
    """Patch priority and/or assigned_session_id on a task.

    Pass `clear_assignment=True` to unset the session. Status transitions go
    through the dedicated functions (promote/block/unblock/done/reopen).
    """
    task = get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")

    changes: dict = {}
    if priority is not None and priority != task.priority:
        conn.execute("UPDATE tasks SET priority=? WHERE id=?", (priority, task_id))
        changes["priority"] = priority
    if clear_assignment:
        if task.assigned_session_id is not None:
            conn.execute(
                "UPDATE tasks SET assigned_session_id=NULL WHERE id=?", (task_id,),
            )
            changes["assigned_session_id"] = None
    elif assigned_session_id is not None and assigned_session_id != task.assigned_session_id:
        conn.execute(
            "UPDATE tasks SET assigned_session_id=? WHERE id=?",
            (assigned_session_id, task_id),
        )
        changes["assigned_session_id"] = assigned_session_id

    if changes:
        conn.execute(
            "UPDATE tasks SET updated_at=? WHERE id=?", (_now_utc(), task_id),
        )
        _append_event(conn, task_id, "updated", actor=actor, payload=changes)
        conn.commit()

    return get_task(conn, task_id)  # type: ignore[return-value]
