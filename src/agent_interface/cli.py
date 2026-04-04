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
    find_session,
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


def _short_id(sid: str, width: int = 8) -> str:
    if len(sid) <= width:
        return sid
    return sid[:width]


def _resolve(query: str) -> Session:
    """Resolve a query to exactly one session, or exit with error."""
    conn = get_connection()
    matches = find_session(conn, query)
    if len(matches) == 0:
        typer.echo(f"No session matching: {query}", err=True)
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(f"Ambiguous — {len(matches)} sessions match '{query}':", err=True)
        for s in matches[:10]:
            typer.echo(f"  {_short_id(s.id, 16):16}  {s.label or '—':16}  {s.cwd or '—'}", err=True)
        raise typer.Exit(1)
    return matches[0]


def _format_table(sessions: list[Session]) -> str:
    if not sessions:
        return "No sessions found."

    headers = ["PID", "LABEL", "STATE", "CWD", "UPDATED"]
    rows: list[list[str]] = []
    for s in sessions:
        state_display = s.state
        if is_stale(s):
            state_display = f"{s.state} (stale)"
        rows.append([
            str(s.pid) if s.pid else _short_id(s.id),
            s.label or "—",
            state_display,
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
def cmd_show(query: str) -> None:
    """Show full details for a session. Matches against id, label, cwd, or pid."""
    s = _resolve(query)
    typer.echo(_format_detail(s))

    conn = get_connection()
    events = list_events(conn, s.id)
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
def cmd_update_state(query: str, state: str) -> None:
    """Update the state of a session."""
    try:
        SessionState(state)
    except ValueError:
        valid = ", ".join(s.value for s in SessionState)
        typer.echo(f"Invalid state: {state}\nValid states: {valid}", err=True)
        raise typer.Exit(1)

    s = _resolve(query)
    conn = get_connection()
    update_state(conn, s.id, state)
    typer.echo(f"State updated: {_short_id(s.id)} → {state}")


@app.command("rename")
def cmd_rename(query: str, label: str) -> None:
    """Rename a session."""
    s = _resolve(query)
    conn = get_connection()
    rename_session(conn, s.id, label)
    typer.echo(f"Renamed: {_short_id(s.id)} → {label}")


@app.command("archive")
def cmd_archive(query: str) -> None:
    """Archive a session."""
    s = _resolve(query)
    conn = get_connection()
    archive_session(conn, s.id)
    typer.echo(f"Archived: {_short_id(s.id)}")


@app.command("restore")
def cmd_restore(query: str) -> None:
    """Restore an archived session."""
    s = _resolve(query)
    conn = get_connection()
    restore_session(conn, s.id)
    typer.echo(f"Restored: {_short_id(s.id)}")


@app.command("hook", hidden=True)
def cmd_hook() -> None:
    """Process a hook event from stdin (called by agent hooks)."""
    from agent_interface.hooks import read_and_process_stdin

    result = read_and_process_stdin()
    # Silent by default — hooks shouldn't produce visible output.
    # Write to stderr only on error for debugging.
    if result.startswith("error:"):
        typer.echo(result, err=True)
        raise typer.Exit(1)


@app.command("init-hooks")
def cmd_init_hooks() -> None:
    """Install agi hooks into ~/.claude/settings.json."""
    from agent_interface.hooks import install_hooks

    ok, msg = install_hooks()
    typer.echo(msg)
    if not ok:
        raise typer.Exit(1)


@app.command("scan")
def cmd_scan() -> None:
    """Scan for running agent sessions and register new ones."""
    from agent_interface.scan import scan_and_register

    hooks_ok, results = scan_and_register()

    if hooks_ok:
        typer.echo("Hooks installed into ~/.claude/settings.json")

    if not results:
        typer.echo("No agent sessions found.")
        return

    registered = [(a, p) for a, p in results if a == "registered"]
    skipped = [(a, p) for a, p in results if a == "skipped"]

    for _, proc in registered:
        tmux = ""
        if proc.tmux_session:
            tmux = f" (tmux {proc.tmux_session}:{proc.tmux_window}.{proc.tmux_pane})"
        typer.echo(f"  + {proc.pid}  {proc.cwd or '?'}{tmux}")

    if registered:
        typer.echo(f"\nRegistered {len(registered)} new session(s).")
    if skipped:
        typer.echo(f"Skipped {len(skipped)} already tracked.")
    if not registered and skipped:
        typer.echo("All found sessions are already tracked.")


def main() -> None:
    app()
