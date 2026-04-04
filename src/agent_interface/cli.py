"""CLI entry point for agi."""

from __future__ import annotations

import uuid
from typing import Optional

import typer

from agent_interface import __version__
from agent_interface.db import get_connection
from agent_interface.models import Session
from agent_interface.registry import (
    archive_session,
    get_session,
    is_stale,
    list_events,
    list_sessions,
    list_waiting,
    register_session,
    rename_session,
    restore_session,
    update_state,
)
from agent_interface.states import SessionState

app = typer.Typer(
    help="agi — Agent Interface for coding agent sessions.",
    invoke_without_command=True,
)


def _short_id(sid: str, width: int = 12) -> str:
    if len(sid) <= width:
        return sid
    return sid[:width] + "…"


def _format_table(sessions: list[Session]) -> str:
    if not sessions:
        return "No sessions found."

    headers = ["ID", "LABEL", "STATE", "HOST", "CWD", "UPDATED"]
    rows: list[list[str]] = []
    for s in sessions:
        state_display = s.state
        if is_stale(s):
            state_display = f"{s.state} (stale)"
        rows.append([
            _short_id(s.id),
            s.label or "—",
            state_display,
            s.host or "—",
            s.cwd or "—",
            s.updated_at or "—",
        ])

    # Compute column widths.
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(c.ljust(col_widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(headers), "─" * (sum(col_widths) + 2 * (len(headers) - 1))]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def _format_detail(s: Session) -> str:
    stale_marker = "  ← stale" if is_stale(s) else ""
    fields = [
        ("id", s.id),
        ("label", s.label or "—"),
        ("state", f"{s.state}{stale_marker}"),
        ("host", s.host or "—"),
        ("cwd", s.cwd or "—"),
        ("repo_root", s.repo_root or "—"),
        ("branch", s.branch or "—"),
        ("tmux", f"{s.tmux_session or '—'}:{s.tmux_window or '—'}:{s.tmux_pane or '—'}"),
        ("worktree", s.worktree_path or "—"),
        ("pid", str(s.pid) if s.pid else "—"),
        ("managed", "yes" if s.is_managed else "no"),
        ("summary", s.summary or "—"),
        ("created_at", s.created_at),
        ("updated_at", s.updated_at),
        ("last_seen_at", s.last_seen_at),
        ("archived_at", s.archived_at or "—"),
    ]
    width = max(len(f[0]) for f in fields)
    return "\n".join(f"  {k:<{width}}  {v}" for k, v in fields)


# ── commands ─────────────────────────────────────────────────────────────────

@app.callback()
def default_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version."),
) -> None:
    """agi — Agent Interface for coding agent sessions."""
    if version:
        typer.echo(f"agi {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # No subcommand → default to list.
        cmd_list(all=False)


@app.command("list")
def cmd_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include done and archived sessions."),
) -> None:
    """List active sessions."""
    conn = get_connection()
    sessions = list_sessions(conn, include_done=all, include_archived=all)
    typer.echo(_format_table(sessions))


@app.command("waiting")
def cmd_waiting() -> None:
    """Show sessions waiting for user input."""
    conn = get_connection()
    sessions = list_waiting(conn)
    typer.echo(_format_table(sessions))


@app.command("show")
def cmd_show(session_id: str) -> None:
    """Show full details for a session."""
    conn = get_connection()
    s = get_session(conn, session_id)
    if s is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)
    typer.echo(_format_detail(s))

    events = list_events(conn, session_id)
    if events:
        typer.echo(f"\n  Events ({len(events)}):")
        for e in events:
            typer.echo(f"    {e.created_at}  {e.event_type}  {e.payload_json or ''}")


@app.command("register")
def cmd_register(
    host: Optional[str] = typer.Option(None),
    cwd: Optional[str] = typer.Option(None),
    label: Optional[str] = typer.Option(None),
    repo_root: Optional[str] = typer.Option(None, "--repo-root"),
    branch: Optional[str] = typer.Option(None),
    tmux_session: Optional[str] = typer.Option(None, "--tmux-session"),
    tmux_window: Optional[str] = typer.Option(None, "--tmux-window"),
    tmux_pane: Optional[str] = typer.Option(None, "--tmux-pane"),
    pid: Optional[int] = typer.Option(None),
    state: str = typer.Option("running"),
    summary: Optional[str] = typer.Option(None),
    id: Optional[str] = typer.Option(None, "--id", help="Custom session id."),
) -> None:
    """Register a new session."""
    session_id = id or uuid.uuid4().hex[:8]

    try:
        SessionState(state)
    except ValueError:
        typer.echo(f"Invalid state: {state}", err=True)
        raise typer.Exit(1)

    session = Session(
        id=session_id,
        label=label,
        host=host,
        cwd=cwd,
        repo_root=repo_root,
        branch=branch,
        tmux_session=tmux_session,
        tmux_window=tmux_window,
        tmux_pane=tmux_pane,
        pid=pid,
        state=state,
        summary=summary,
    )
    conn = get_connection()
    register_session(conn, session)
    typer.echo(f"Registered session: {session_id}")


@app.command("update-state")
def cmd_update_state(session_id: str, state: str) -> None:
    """Update the state of a session."""
    try:
        SessionState(state)
    except ValueError:
        valid = ", ".join(s.value for s in SessionState)
        typer.echo(f"Invalid state: {state}\nValid states: {valid}", err=True)
        raise typer.Exit(1)

    conn = get_connection()
    s = update_state(conn, session_id, state)
    if s is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"State updated: {session_id} → {state}")


@app.command("rename")
def cmd_rename(session_id: str, label: str) -> None:
    """Rename a session."""
    conn = get_connection()
    s = rename_session(conn, session_id, label)
    if s is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Renamed: {session_id} → {label}")


@app.command("archive")
def cmd_archive(session_id: str) -> None:
    """Archive a session."""
    conn = get_connection()
    s = archive_session(conn, session_id)
    if s is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Archived: {session_id}")


@app.command("restore")
def cmd_restore(session_id: str) -> None:
    """Restore an archived session."""
    conn = get_connection()
    s = restore_session(conn, session_id)
    if s is None:
        typer.echo(f"Session not found: {session_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Restored: {session_id}")


def main() -> None:
    app()
