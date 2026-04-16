"""Dispatch — spawn Claude sessions bound to tasks.

Creates a tmux window per task, sets AGI_TASK_ID + AGI_SESSION_ID, and
launches `claude`. The SessionStart hook fires on boot, injecting the
assignment via MCP/additionalContext.

Worktrees are created when dispatching from inside a git repo (opt-out
with worktree=False). Non-git directories share the cwd.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass
from typing import Optional

from agent_interface.db import get_connection as _base_conn
from agent_interface.models import Session
from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import get_connection
from agent_interface.registry import register_session


@dataclass
class DispatchResult:
    task_id: str
    session_id: str
    tmux_target: str
    worktree_path: Optional[str]


def _is_git_repo(path: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=path, capture_output=True, timeout=5,
    )
    return r.returncode == 0


def _repo_root(path: str) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path, capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip()


def _current_branch(path: str) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path, capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip()


def _create_worktree(repo: str, task_id: str) -> str:
    wt_dir = os.path.join(repo, ".worktrees", task_id)
    branch = f"task/{task_id}"
    base = _current_branch(repo)
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, wt_dir, base],
        cwd=repo, capture_output=True, timeout=30, check=True,
    )
    return wt_dir


def _tmux_session_name() -> str:
    """Current tmux session or 'agi' as fallback."""
    name = os.environ.get("TMUX")
    if name:
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "#{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return "agi"


def dispatch_task(
    task_id: str,
    *,
    cwd: Optional[str] = None,
    worktree: bool = True,
    prompt: Optional[str] = None,
) -> DispatchResult:
    """Spawn a Claude session in tmux bound to a single task.

    Returns DispatchResult with the session info.
    """
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is required for dispatch")
    if not shutil.which("claude"):
        raise RuntimeError("claude CLI is required for dispatch")

    conn = get_connection()
    task = core.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status not in ("ready", "in_progress"):
        raise ValueError(f"Task {task_id} is {task.status}, must be ready or in_progress")

    base_cwd = cwd or os.getcwd()
    session_id = uuid.uuid4().hex[:12]
    hostname = socket.gethostname()
    wt_path = None

    # Create worktree if in a git repo and opted in.
    if worktree and _is_git_repo(base_cwd):
        repo = _repo_root(base_cwd)
        try:
            wt_path = _create_worktree(repo, task.id)
        except subprocess.CalledProcessError:
            wt_path = None  # fall back to shared cwd

    work_dir = wt_path or base_cwd

    # Register the session before spawning so hooks can find it.
    session = Session(
        id=session_id,
        label=task.title[:60],
        host=hostname,
        cwd=work_dir,
        repo_root=_repo_root(work_dir) if _is_git_repo(work_dir) else None,
        state="running",
        is_managed=True,
        worktree_path=wt_path,
    )
    base_conn = _base_conn()
    register_session(base_conn, session)

    # Bind task → session.
    conn.execute(
        "UPDATE tasks SET assigned_session_id=?, status=?, worktree_path=?,"
        " updated_at=datetime('now') WHERE id=?",
        (session_id, "in_progress", wt_path, task.id),
    )
    conn.commit()

    # Build the claude launch command.
    safe_title = task.title.replace('"', '\\"')[:50]
    initial_prompt = prompt or (
        f"You have been assigned task {task.id}: {safe_title}."
        " Read your assignment from the get_assignment MCP tool,"
        " then begin working. Call done when complete or block if stuck."
    )

    env_exports = (
        f'export AGI_TASK_ID="{task.id}" '
        f'AGI_SESSION_ID="{session_id}" '
        f'AGI_DB_PATH="{os.environ.get("AGI_DB_PATH", "")}"'
    )

    tmux_session = _tmux_session_name()
    window_name = task.id[:12]

    # Create tmux window.
    subprocess.run(
        ["tmux", "new-window", "-t", tmux_session, "-n", window_name],
        capture_output=True, timeout=10,
    )

    # Send commands to the new window.
    target = f"{tmux_session}:{window_name}"
    cmds = [
        f"cd {work_dir}",
        env_exports,
        f'claude -p "{initial_prompt}"',
    ]
    for cmd in cmds:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, cmd, "Enter"],
            capture_output=True, timeout=5,
        )

    return DispatchResult(
        task_id=task.id,
        session_id=session_id,
        tmux_target=target,
        worktree_path=wt_path,
    )


def dispatch_project(
    project: str,
    n: int = 1,
    *,
    cwd: Optional[str] = None,
    worktree: bool = True,
    tags: Optional[list[str]] = None,
) -> list[DispatchResult]:
    """Dispatch up to N ready tasks from a project."""
    conn = get_connection()
    tasks = core.list_tasks(conn, project=project, status="ready")

    if tags:
        tasks = [t for t in tasks if all(tag in t.tags for tag in tags)]

    results = []
    for task in tasks[:n]:
        result = dispatch_task(
            task.id,
            cwd=cwd,
            worktree=worktree,
        )
        results.append(result)

    return results
