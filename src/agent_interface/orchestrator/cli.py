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
def tasks_show(task_id: str) -> None:
    """Show full task detail + event log."""
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
    if events:
        console.print(f"\n  [bold]Events ({len(events)}):[/bold]")
        for e in events:
            pl = f"  [dim]{e.payload_json}[/dim]" if e.payload_json else ""
            console.print(
                f"    [dim]{e.created_at}[/dim]  {e.event_type}"
                f"  [dim]({e.actor})[/dim]{pl}"
            )
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


def cmd_board(
    project: Optional[str] = typer.Argument(None),
) -> None:
    """Kanban view grouped by status."""
    conn = get_connection()
    tasks = core.list_tasks(conn, project=project, include_closed=False)

    if not tasks:
        console.print("[dim]No open tasks.[/dim]")
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
    ]

    header = "─── board"
    if project:
        header += f": {project}"
    header += f" · {len(tasks)} open ───"
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
