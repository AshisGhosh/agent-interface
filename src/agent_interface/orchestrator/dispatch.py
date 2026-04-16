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


def _create_worktree(
    repo: str, task_id: str, base_ref: Optional[str] = None,
) -> str:
    """Create or reuse a worktree for this task.

    Idempotent: if .worktrees/<task-id> already exists and is a valid git
    worktree for task/<task-id>, reuse it. If stale state is in the way
    (bare directory without git worktree registration, or branch without
    worktree), clean up before re-creating.

    base_ref: branch/ref to branch the task off of (default: repo's current
    branch, typically main). Passing a dep task's branch gives dep-aware
    branching — the task inherits its dependency's work.
    """
    wt_dir = os.path.join(repo, ".worktrees", task_id)
    branch = f"task/{task_id}"
    base = base_ref or _current_branch(repo)

    # Check current git worktree registration.
    registered = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo, capture_output=True, text=True, timeout=10,
    )
    registered_paths = {
        ln.split(" ", 1)[1]
        for ln in registered.stdout.splitlines()
        if ln.startswith("worktree ")
    }

    branch_exists = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", branch],
        cwd=repo, capture_output=True, timeout=10,
    ).returncode == 0

    # Fast path: registered + dir exists → reuse.
    if wt_dir in registered_paths and os.path.isdir(wt_dir):
        return wt_dir

    # Clean up stale state so `worktree add` can succeed.
    if os.path.isdir(wt_dir) or wt_dir in registered_paths:
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt_dir],
            cwd=repo, capture_output=True, timeout=30,
        )
        if os.path.isdir(wt_dir):
            import shutil as _sh
            _sh.rmtree(wt_dir, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo, capture_output=True, timeout=10,
    )

    # Create the worktree — reuse existing branch if present, create otherwise.
    cmd = ["git", "worktree", "add"]
    if branch_exists:
        cmd.extend([wt_dir, branch])
    else:
        cmd.extend(["-b", branch, wt_dir, base])
    subprocess.run(cmd, cwd=repo, capture_output=True, timeout=30, check=True)
    return wt_dir


def _cleanup_zombie_mcp_processes() -> int:
    """Kill agi mcp / hook processes whose parent is init (orphaned).

    When a dispatched agent crashes or is killed ungracefully, its MCP
    server (child of claude) can survive and hold DB connections. These
    orphans cause lock contention. Called before each dispatch as a
    cleanup pass.

    Returns number of processes killed.
    """
    import signal
    from pathlib import Path as _P

    killed = 0
    for proc in _P("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="ignore")
            if "agi mcp" not in cmdline and "agi hook" not in cmdline:
                continue
            stat = (proc / "stat").read_text()
            # parent pid is field 4 after comm (which can contain spaces)
            close_paren = stat.rfind(")")
            ppid = int(stat[close_paren + 2:].split()[1])
            if ppid == 1:  # orphaned (reparented to init)
                os.kill(int(proc.name), signal.SIGTERM)
                killed += 1
        except (OSError, ValueError, IndexError):
            continue
    return killed


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
    # Clean up any stale in_progress bindings first. Agents whose sessions
    # exited cleanly will have their task reset to ready.
    core.reap_orphaned_tasks(conn)
    task = core.get_task(conn, task_id)
    if task is None:
        raise ValueError(f"No such task: {task_id}")
    if task.status != "ready":
        raise ValueError(
            f"Task {task_id} is {task.status}, must be ready to dispatch "
            f"(currently assigned to session: {task.assigned_session_id or 'none'})"
        )

    base_cwd = cwd or os.getcwd()
    session_id = uuid.uuid4().hex[:12]
    hostname = socket.gethostname()
    wt_path = None

    # Create worktree if in a git repo and opted in.
    if worktree and _is_git_repo(base_cwd):
        repo = _repo_root(base_cwd)
        # Dep-aware branching: if the task has a 'done' dependency whose
        # branch exists, branch from it so the agent inherits its work.
        # With multiple done deps we pick the most-recently-closed one
        # (good enough heuristic; agents can manually merge others).
        base_ref = None
        if task.depends_on:
            for dep_id in task.depends_on:
                dep = core.get_task(conn, dep_id)
                if dep is None or dep.status != "done":
                    continue
                dep_branch = f"task/{dep_id}"
                exists = subprocess.run(
                    ["git", "rev-parse", "--verify", "--quiet", dep_branch],
                    cwd=repo, capture_output=True, timeout=10,
                )
                if exists.returncode == 0:
                    base_ref = dep_branch
                    # Keep looking for a more-recently-closed dep.
        try:
            wt_path = _create_worktree(repo, task.id, base_ref=base_ref)
        except subprocess.CalledProcessError:
            wt_path = None  # fall back to shared cwd

    work_dir = wt_path or base_cwd

    # Uniform label: 'agi/<task-id> <short-title>' so all dispatched agents
    # are easy to recognise in `agi list`. 60 chars total.
    agent_label = f"agi/{task.id} {task.title}"[:60]

    tmux_session = _tmux_session_name()
    window_name = task.id[:12]

    # Register the session before spawning so hooks can find it. Include the
    # tmux coordinates so `agi jump <session>` works out of the box.
    session = Session(
        id=session_id,
        label=agent_label,
        host=hostname,
        cwd=work_dir,
        repo_root=_repo_root(work_dir) if _is_git_repo(work_dir) else None,
        state="running",
        is_managed=True,
        worktree_path=wt_path,
        tmux_session=tmux_session,
        tmux_window=window_name,
        tmux_pane="0",
    )
    base_conn = _base_conn()
    register_session(base_conn, session)

    # Log path must be known before we record the dispatched event.
    log_dir = os.path.expanduser("~/.local/share/agent-interface/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{task.id}-{session_id}.log")

    # Bind task → session + append a 'dispatched' event with metadata
    # (log path, tmux target, worktree) for post-mortem access.
    conn.execute(
        "UPDATE tasks SET assigned_session_id=?, status=?, worktree_path=?,"
        " updated_at=datetime('now') WHERE id=?",
        (session_id, "in_progress", wt_path, task.id),
    )
    conn.execute(
        """INSERT INTO task_events (task_id, event_type, actor, payload_json, created_at)
           VALUES (?, 'dispatched', ?, ?, datetime('now'))""",
        (
            task.id,
            f"session:{session_id}",
            __import__("json").dumps({
                "session_id": session_id,
                "worktree_path": wt_path,
                "log_path": log_path,
                "tmux_target": f"{tmux_session}:{window_name}",
            }),
        ),
    )
    conn.commit()

    # Build the prompt. Stored in a temp file so we don't have to escape
    # anything for the shell — no backtick, $, newline, or quote hazards.
    safe_title = task.title[:80]
    initial_prompt = prompt or (
        f"You are an autonomous agent working on task {task.id}: {safe_title}. "
        "Call the get_assignment MCP tool first to read the full task details. "
        "Then implement the task end-to-end using the available file-editing and "
        "shell tools. You are NOT interactive -- there is no user to ask questions. "
        "Make reasonable decisions autonomously. Only call block if a hard "
        "external dependency prevents progress (missing credentials, unreachable "
        "services, corrupted state) -- never block for waiting on clarification "
        "or not sure what the user wants. You are working in a git worktree; "
        "BEFORE calling done, make sure all tests pass and any required lint "
        "checks are clean so the auto-commit will succeed. The system will "
        "auto-commit your changes when you call done; if the commit fails "
        "(pre-commit hook failures), the task moves to review and the user "
        "sees the error. Use progress to report milestones along the way. "
        "When the implementation is complete and tested, call done with a "
        "summary of what shipped."
    )

    prompt_path = f"/tmp/agi-prompt-{task.id}-{session_id}.txt"
    with open(prompt_path, "w") as pf:
        pf.write(initial_prompt)

    env_prefix = (
        f'AGI_TASK_ID="{task.id}" '
        f'AGI_SESSION_ID="{session_id}" '
        f'AGI_DB_PATH="{os.environ.get("AGI_DB_PATH", "")}"'
    )

    # Single shell command: cd, run claude (output tee'd to log), window
    # closes when claude exits so we don't accumulate stale tmux windows.
    shell_cmd = (
        f"cd {work_dir} && "
        f'{env_prefix} claude --dangerously-skip-permissions -p "$(cat {prompt_path})" '
        f'2>&1 | tee {log_path}; '
        f"rm -f {prompt_path}"
    )

    target = f"{tmux_session}:{window_name}"
    # Trailing ':' disambiguates session reference — 'tmux new-window -t 5'
    # would be parsed as session:window=5, colliding with a real window.
    session_target = f"{tmux_session}:"

    # Create tmux window with the command inline. This is more reliable than
    # send-keys for long strings — tmux runs the command directly.
    result = subprocess.run(
        ["tmux", "new-window", "-t", session_target, "-n", window_name, shell_cmd],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        # Surface failure rather than returning a phantom dispatch.
        raise RuntimeError(
            f"tmux new-window failed: {result.stderr.strip() or 'unknown error'}",
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
    """Dispatch up to N ready tasks from a project.

    Before dispatching:
    - Kills orphaned agi mcp/hook processes so they don't hold DB locks
    - Reaps tasks whose sessions are explicitly done/archived
    """
    _cleanup_zombie_mcp_processes()

    conn = get_connection()
    core.reap_orphaned_tasks(conn)
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
