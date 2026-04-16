"""CLI subcommands for the orchestrator.

Hot-path verbs (`next`, `done`, `block`, `progress`, `board`) are attached to
the top-level `agi` app. Admin verbs are grouped under `tasks` and `projects`
sub-apps.
"""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import get_connection
from agent_interface.orchestrator.models import Task
from agent_interface.orchestrator.states import TaskStatus

console = Console()

projects_app = typer.Typer(help="Manage orchestrator projects.")
tasks_app = typer.Typer(help="Manage tasks.")


# ── helpers ──────────────────────────────────────────────────────────────────

STATUS_STYLES = {
    TaskStatus.BACKLOG.value: "dim",
    TaskStatus.READY.value: "cyan",
    TaskStatus.IN_PROGRESS.value: "green",
    TaskStatus.REVIEW.value: "yellow",
    TaskStatus.BLOCKED.value: "red",
    TaskStatus.DONE.value: "dim green",
}


def _current_session_id() -> Optional[str]:
    """Best-effort resolution of the current session id.

    Priority: AGI_SESSION_ID env (set by dispatch) → pid ancestry lookup.
    """
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


def _print_tasks(tasks: list[Task]) -> None:
    if not tasks:
        console.print("[dim]No tasks.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("P", justify="right", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("TITLE", max_width=60, overflow="ellipsis")
    table.add_column("TAGS", style="dim", no_wrap=True)
    table.add_column("SESSION", style="dim", no_wrap=True)

    for t in tasks:
        style = STATUS_STYLES.get(t.status, "")
        table.add_row(
            t.id,
            f"p{t.priority}",
            f"[{style}]{t.status}[/{style}]",
            t.title,
            ",".join(t.tags) if t.tags else "—",
            t.assigned_session_id or "—",
        )
    console.print(table)


def _require_task(conn, task_id: str) -> Task:
    t = core.get_task(conn, task_id)
    if t is None:
        console.print(f"[red]No such task:[/red] {task_id}")
        raise typer.Exit(1)
    return t


# ── projects subcommands ─────────────────────────────────────────────────────

@projects_app.command("new")
def projects_new(
    name: str,
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    autonomy: str = typer.Option("none", "--autonomy"),
) -> None:
    """Create a new project."""
    conn = get_connection()
    p = core.create_project(conn, name, description=description, autonomy=autonomy)
    console.print(f"Created project [cyan]{p.id}[/cyan]: {p.name}")


@projects_app.command("list")
def projects_list(
    all: bool = typer.Option(False, "--all", help="Include archived."),
) -> None:
    """List projects."""
    conn = get_connection()
    projects = core.list_projects(conn, include_archived=all)
    if not projects:
        console.print("[dim]No projects.[/dim]")
        return
    for p in projects:
        arch = " [dim](archived)[/dim]" if p.archived_at else ""
        desc = f"  [dim]{p.description}[/dim]" if p.description else ""
        console.print(f"  [cyan]{p.id}[/cyan]  [bold]{p.name}[/bold]{arch}{desc}")


# ── tasks subcommands ────────────────────────────────────────────────────────

@tasks_app.command("add")
def tasks_add(
    title: str,
    project: str = typer.Option(..., "--project", "-p"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    priority: int = typer.Option(2, "--priority"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated."),
    depends_on: Optional[str] = typer.Option(
        None, "--depends-on", help="Comma-separated task ids.",
    ),
) -> None:
    """Add a task."""
    conn = get_connection()
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    dep_list = [d.strip() for d in depends_on.split(",")] if depends_on else []
    try:
        t = core.add_task(
            conn, project, title,
            description=description,
            priority=priority,
            tags=tag_list,
            depends_on=dep_list,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    status = t.status.value if hasattr(t.status, "value") else t.status
    style = STATUS_STYLES.get(status, "")
    console.print(f"Added [cyan]{t.id}[/cyan]  [{style}]{status}[/]  {t.title}")


@tasks_app.command("list")
def tasks_list(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    status: Optional[str] = typer.Option(None, "--status", "-s"),
    all: bool = typer.Option(False, "--all", help="Include done."),
) -> None:
    """List tasks."""
    conn = get_connection()
    if status:
        try:
            TaskStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in TaskStatus)
            console.print(f"[red]Invalid status:[/red] {status}\nValid: {valid}")
            raise typer.Exit(1)
    _print_tasks(core.list_tasks(conn, project=project, status=status, include_closed=all))


@tasks_app.command("show")
def tasks_show(
    task_id: str,
    log_lines: int = typer.Option(
        30, "--log-lines", help="Lines of agent output log to show (0=none)."
    ),
) -> None:
    """Show full task detail + event log + agent output tail."""
    import json as _json

    conn = get_connection()
    t = _require_task(conn, task_id)

    console.print()
    console.print(f"  [bold]id[/bold]         {t.id}")
    console.print(f"  [bold]project[/bold]    {t.project_id}")
    console.print(f"  [bold]title[/bold]      {t.title}")
    style = STATUS_STYLES.get(t.status, "")
    console.print(f"  [bold]status[/bold]     [{style}]{t.status}[/{style}]")
    console.print(f"  [bold]priority[/bold]   p{t.priority}")
    console.print(f"  [bold]tags[/bold]       {','.join(t.tags) if t.tags else '—'}")
    console.print(f"  [bold]creator[/bold]    {t.creator}")
    console.print(f"  [bold]session[/bold]    {t.assigned_session_id or '—'}")
    console.print(f"  [bold]deps[/bold]       {','.join(t.depends_on) if t.depends_on else '—'}")
    console.print(f"  [bold]parent[/bold]     {t.parent_id or '—'}")
    if t.description:
        console.print()
        console.print(f"  {t.description}")

    events = core.list_events(conn, t.id)

    # Pull log path from the most recent dispatched event.
    log_path = None
    for e in reversed(events):
        if e.event_type == "dispatched" and e.payload_json:
            try:
                log_path = _json.loads(e.payload_json).get("log_path")
                break
            except Exception:
                pass
    if log_path:
        console.print(f"  [bold]log[/bold]        {log_path}")

    if events:
        console.print(f"\n  [bold]Events ({len(events)}):[/bold]")
        for e in events:
            pl = f"  [dim]{e.payload_json}[/dim]" if e.payload_json else ""
            console.print(
                f"    [dim]{e.created_at}[/dim]  {e.event_type}"
                f"  [dim]({e.actor})[/dim]{pl}"
            )

    if log_path and log_lines > 0 and os.path.exists(log_path):
        try:
            with open(log_path) as f:
                lines = f.readlines()
            tail = lines[-log_lines:]
            if tail:
                console.print(
                    f"\n  [bold]Agent output (last {len(tail)} lines):[/bold]"
                )
                for line in tail:
                    console.print(f"    {line.rstrip()}", highlight=False)
        except OSError:
            pass
    console.print()


@tasks_app.command("promote")
def tasks_promote(task_id: str) -> None:
    """Move a backlog task to ready (claimable)."""
    conn = get_connection()
    try:
        t = core.promote(conn, task_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Promoted [cyan]{t.id}[/cyan] → ready")


@tasks_app.command("diff")
def tasks_diff(task_id: str) -> None:
    """Show the git diff for a task's worktree (vs. its base branch)."""
    import subprocess as _sub
    conn = get_connection()
    t = _require_task(conn, task_id)
    if not t.worktree_path:
        console.print("[dim]No worktree for this task.[/dim]")
        raise typer.Exit(0)

    # Show uncommitted + committed-on-task-branch vs main.
    # Committed diff:
    r = _sub.run(
        ["git", "log", "--oneline", "main..HEAD"],
        cwd=t.worktree_path, capture_output=True, text=True, timeout=15,
    )
    if r.stdout.strip():
        console.print("[bold]Commits on this branch:[/bold]")
        console.print(r.stdout)

    # Uncommitted:
    r = _sub.run(
        ["git", "status", "--short"],
        cwd=t.worktree_path, capture_output=True, text=True, timeout=15,
    )
    if r.stdout.strip():
        console.print("[bold]Uncommitted:[/bold]")
        console.print(r.stdout)

    # Diff vs main (committed changes):
    r = _sub.run(
        ["git", "diff", "main...HEAD", "--stat"],
        cwd=t.worktree_path, capture_output=True, text=True, timeout=15,
    )
    if r.stdout.strip():
        console.print("[bold]Diff vs main (committed):[/bold]")
        console.print(r.stdout)


# ── hot-path verbs (attached to top-level app elsewhere) ─────────────────────

def cmd_next(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    tags: Optional[str] = typer.Option(None, "--tags"),
) -> None:
    """Claim the next available task for the current session."""
    conn = get_connection()
    session_id = _current_session_id()
    if session_id is None:
        console.print("[red]Could not determine current session.[/red]")
        console.print("[dim]Set AGI_SESSION_ID or run from a tracked agent session.[/dim]")
        raise typer.Exit(1)

    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    t = core.claim_next(conn, session_id, project=project, tags=tag_list)
    if t is None:
        console.print("[dim]No ready tasks.[/dim]")
        raise typer.Exit(0)

    console.print(f"Claimed [cyan]{t.id}[/cyan]: [bold]{t.title}[/bold]")
    if t.description:
        console.print()
        console.print(t.description)


def cmd_progress(
    task_id: str,
    note: str = typer.Argument(..., help="Progress note."),
    pct: Optional[int] = typer.Option(None, "--pct"),
) -> None:
    """Report progress on a task."""
    conn = get_connection()
    session_id = _current_session_id()
    actor = f"session:{session_id}" if session_id else "user"
    try:
        core.progress(conn, task_id, note, pct=pct, actor=actor)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Progress recorded on [cyan]{task_id}[/cyan].")


def cmd_block(
    task_id: str,
    reason: str = typer.Option(..., "--reason", "-r"),
    needs: str = typer.Option("user", "--needs", help="user|dep|resource"),
) -> None:
    """Mark a task blocked."""
    conn = get_connection()
    session_id = _current_session_id()
    actor = f"session:{session_id}" if session_id else "user"
    try:
        core.block_task(conn, task_id, reason, needs=needs, actor=actor)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Blocked [cyan]{task_id}[/cyan] (needs={needs}).")


def cmd_unblock(task_id: str) -> None:
    """Unblock a task."""
    conn = get_connection()
    try:
        t = core.unblock_task(conn, task_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Unblocked [cyan]{task_id}[/cyan] → {t.status}")


def cmd_done(
    task_id: str,
    summary: str = typer.Option(..., "--summary", "-s"),
) -> None:
    """Mark a task done."""
    conn = get_connection()
    session_id = _current_session_id()
    actor = f"session:{session_id}" if session_id else "user"
    try:
        core.done_task(conn, task_id, summary, actor=actor)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Done [cyan]{task_id}[/cyan].")


def cmd_dispatch(
    project: str = typer.Argument(..., help="Project name or id."),
    n: int = typer.Option(1, "--n", "-n", help="Number of agents to spawn."),
    no_worktree: bool = typer.Option(False, "--no-worktree", help="Skip git worktree creation."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tag filter."),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory (default: current)."),
) -> None:
    """Dispatch agents to work on ready tasks from a project."""
    from agent_interface.orchestrator.dispatch import dispatch_project

    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    try:
        results = dispatch_project(
            project, n,
            cwd=cwd,
            worktree=not no_worktree,
            tags=tag_list,
        )
    except (RuntimeError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if not results:
        console.print("[dim]No ready tasks to dispatch.[/dim]")
        return

    for r in results:
        wt = f"  [dim]worktree: {r.worktree_path}[/dim]" if r.worktree_path else ""
        console.print(f"  [green]+[/green] [cyan]{r.task_id}[/cyan] → {r.tmux_target}{wt}")
    console.print(
        f"\nDispatched {len(results)} agent(s)."
        f" Use [bold]agi board {project}[/bold] to watch."
    )


def cmd_review(
    project: Optional[str] = typer.Argument(None, help="Filter by project."),
) -> None:
    """List tasks awaiting review (typically auto-commit failures).

    Use `agi tasks diff <id>` to inspect changes, `agi tasks show <id>` for
    the event log and failure reason, then `agi approve <id>` or
    `agi reject <id> --reason ...`.
    """
    conn = get_connection()
    tasks = core.list_tasks(conn, project=project, status=TaskStatus.REVIEW.value)
    if not tasks:
        console.print("[dim]No tasks in review.[/dim]")
        return

    console.print(f"─── {len(tasks)} task(s) awaiting review ───\n")
    for t in tasks:
        console.print(
            f"[cyan]{t.id}[/cyan]  [yellow]{t.status}[/yellow]  "
            f"p{t.priority}  [bold]{t.title}[/bold]",
        )
        if t.worktree_path:
            console.print(f"    [dim]worktree: {t.worktree_path}[/dim]")
        # Show latest review_requested event reason if any.
        events = core.list_events(conn, t.id)
        for e in reversed(events):
            if e.event_type == "review_requested" and e.payload_json:
                import json as _j
                try:
                    p = _j.loads(e.payload_json)
                    reason = p.get("reason", "?")
                    err = p.get("error", "")
                    console.print(f"    [dim]reason:[/dim] {reason}")
                    if err:
                        err_preview = err[:300].replace("\n", "\n      ")
                        console.print(f"    [dim]error:[/dim] {err_preview}")
                except Exception:
                    pass
                break
        console.print()


def cmd_approve(
    task_id: str,
) -> None:
    """Approve a task in review → done."""
    conn = get_connection()
    try:
        t = core.approve_review(conn, task_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Approved [cyan]{t.id}[/cyan] → done")


def cmd_reject(
    task_id: str,
    reason: str = typer.Option(..., "--reason", "-r"),
) -> None:
    """Reject a task in review → back to in_progress/ready."""
    conn = get_connection()
    try:
        t = core.reject_review(conn, task_id, reason)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Rejected [cyan]{t.id}[/cyan] → {t.status}")


def cmd_watch(
    target: Optional[str] = typer.Argument(
        None,
        help="Project name or task id. Empty = watch all projects.",
    ),
    poll: float = typer.Option(5.0, "--poll", help="Poll interval in seconds."),
) -> None:
    """Tail meaningful task events in real-time.

    Emits one line per semantic event (dispatched, progress, blocked, done,
    review requested, auto-promotion). Skips low-signal noise like state
    heartbeats, tool-call counts, session_orphaned transitions.

    Ctrl-C to exit.
    """
    import json as _json
    import signal
    import time

    SEMANTIC = {
        "dispatched",
        "progress",
        "done",
        "blocked",
        "unblocked",
        "review_requested",
        "approved",
        "rejected",
        "ready",       # auto-promotion → dependent task unlocked
        "reopened",
    }

    # Resolve target.
    project_filter = None
    task_filter = None
    if target:
        # Task ids start with 't-'; projects have any other shape.
        conn = get_connection()
        t = core.get_task(conn, target)
        if t:
            task_filter = target
        else:
            p = core.get_project(conn, target)
            if p is None:
                console.print(f"[red]No such project or task:[/red] {target}")
                raise typer.Exit(1)
            project_filter = p.id

    # Prepare formatter and starting cursor.
    def _fmt(event_type: str, task_id: str, title: str, payload: dict) -> str:
        title = title[:40]
        if event_type == "dispatched":
            tmux = payload.get("tmux_target", "")
            return f"[cyan]▶ dispatched[/cyan]    [bold]{task_id}[/bold] {title}  [dim]{tmux}[/dim]"
        if event_type == "progress":
            pct = payload.get("pct")
            note = payload.get("note", "")[:80]
            badge = f"{pct}%" if pct is not None else ""
            return (
                f"[green]· progress {badge}[/green]   "
                f"[bold]{task_id}[/bold] {title}  [dim]{note}[/dim]"
            )
        if event_type == "done":
            summary = payload.get("summary", "").split("\n")[0][:80]
            commit = payload.get("commit", {})
            sha = commit.get("sha", "")
            sha_bit = f"[dim]@{sha}[/dim]" if sha else ""
            return (
                f"[bold green]✓ done[/bold green]          "
                f"[bold]{task_id}[/bold] {title} {sha_bit}  [dim]{summary}[/dim]"
            )
        if event_type == "blocked":
            reason = payload.get("reason", "?")
            needs = payload.get("needs", "user")
            return (
                f"[bold red]⏸ blocked[/bold red]       "
                f"[bold]{task_id}[/bold] {title}  "
                f"[red]needs={needs}[/red]  [dim]{reason}[/dim]"
            )
        if event_type == "unblocked":
            return f"[cyan]▶ unblocked[/cyan]     [bold]{task_id}[/bold] {title}"
        if event_type == "review_requested":
            reason = payload.get("reason", "?")
            return (
                f"[bold yellow]⚠ review[/bold yellow]        "
                f"[bold]{task_id}[/bold] {title}  [dim]{reason}[/dim]"
            )
        if event_type == "approved":
            return f"[bold green]✓ approved[/bold green]      [bold]{task_id}[/bold] {title}"
        if event_type == "rejected":
            return f"[bold red]✗ rejected[/bold red]      [bold]{task_id}[/bold] {title}"
        if event_type == "ready":
            trigger = payload.get("trigger", "")
            trig = f" [dim](unblocked by {trigger})[/dim]" if trigger else ""
            return f"[cyan]↑ ready[/cyan]         [bold]{task_id}[/bold] {title}{trig}"
        if event_type == "reopened":
            return f"[yellow]↻ reopened[/yellow]      [bold]{task_id}[/bold] {title}"
        return f"{event_type}  {task_id} {title}"

    placeholders = ",".join("?" for _ in SEMANTIC)
    base_query = f"""
        SELECT e.id, e.task_id, e.event_type, e.payload_json, e.created_at,
               t.title, t.project_id
          FROM task_events e
          JOIN tasks t ON t.id = e.task_id
         WHERE e.event_type IN ({placeholders})
           AND e.id > ?
    """
    if project_filter:
        base_query += " AND t.project_id = ?"
    if task_filter:
        base_query += " AND e.task_id = ?"
    base_query += " ORDER BY e.id"

    # Start from the latest existing event id so we don't dump history.
    conn = get_connection()
    row = conn.execute("SELECT MAX(id) as m FROM task_events").fetchone()
    last_id = row["m"] or 0

    header_target = task_filter or project_filter or "all projects"
    console.print(f"[dim]watching {header_target}  [poll={poll}s]  ctrl-c to exit[/dim]")

    stop = False

    def _handle_sigint(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sigint)

    while not stop:
        try:
            conn = get_connection()
            args = list(SEMANTIC) + [last_id]
            if project_filter:
                args.append(project_filter)
            if task_filter:
                args.append(task_filter)
            rows = conn.execute(base_query, args).fetchall()

            for r in rows:
                payload = {}
                if r["payload_json"]:
                    try:
                        payload = _json.loads(r["payload_json"])
                    except Exception:
                        pass
                line = _fmt(r["event_type"], r["task_id"], r["title"], payload)
                console.print(line, highlight=False)
                last_id = r["id"]
        except Exception as e:
            console.print(f"[dim red]watch error: {e}[/dim red]")

        # Sleep in small slices so Ctrl-C is responsive.
        slept = 0.0
        while slept < poll and not stop:
            time.sleep(min(0.3, poll - slept))
            slept += 0.3


def cmd_board(
    project: Optional[str] = typer.Argument(None),
    all: bool = typer.Option(False, "--all", "-a", help="Include done tasks."),
) -> None:
    """Kanban view grouped by status. Use --all to include done tasks."""
    conn = get_connection()
    tasks = core.list_tasks(conn, project=project, include_closed=all)

    if not tasks:
        console.print("[dim]No tasks.[/dim]")
        return

    # Group by status.
    by_status: dict[str, list[Task]] = {}
    for t in tasks:
        by_status.setdefault(t.status, []).append(t)

    order = [
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.REVIEW.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.READY.value,
        TaskStatus.BACKLOG.value,
        TaskStatus.DONE.value,
    ]

    open_count = sum(1 for t in tasks if t.status != TaskStatus.DONE.value)
    done_count = len(tasks) - open_count
    header = "─── board"
    if project:
        header += f": {project}"
    header += f" · {open_count} open"
    if done_count:
        header += f" · {done_count} done"
    header += " ───"
    console.print(header)

    for status in order:
        group = by_status.get(status, [])
        if not group:
            continue
        style = STATUS_STYLES.get(status, "")
        console.print()
        console.print(f"[{style}]{status}[/{style}] [dim]({len(group)})[/dim]")
        for t in group:
            assignee = f" [dim]@{t.assigned_session_id}[/dim]" if t.assigned_session_id else ""
            tg = f" [dim]{','.join(t.tags)}[/dim]" if t.tags else ""
            console.print(
                f"  [cyan]{t.id}[/cyan]  [dim]p{t.priority}[/dim]"
                f"  {t.title}{tg}{assignee}"
            )
    console.print()
