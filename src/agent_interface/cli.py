"""CLI entry point for agi."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

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
    context_settings={"help_option_names": ["-h", "--help"]},
)

console = Console()

# ── display helpers ──────────────────────────────────────────────────────────

STATE_STYLES: dict[str, str] = {
    "waiting_for_user": "bold yellow",
    "running": "green",
    "blocked": "red",
    "tests_failed": "red",
    "done": "dim",
    "idle": "dim",
    "archived": "dim",
    "stale": "dim red",
    "unknown": "dim",
}

STATE_SHORT: dict[str, str] = {
    "waiting_for_user": "waiting",
    "tests_failed": "tests",
}


def _short_id(sid: str, width: int = 8) -> str:
    if len(sid) <= width:
        return sid
    return sid[:width]


def _compact_cwd(path: str) -> str:
    """Shorten paths like /home/user/foo to ~/foo."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _relative_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except (ValueError, AttributeError):
        return iso_str or "—"


def _state_text(state: str, stale: bool = False) -> Text:
    display = STATE_SHORT.get(state, state)
    if stale:
        display = f"{display} (stale)"
        style = STATE_STYLES.get("stale", "")
    else:
        style = STATE_STYLES.get(state, "")
    return Text(display, style=style)


def _resolve(query: str) -> Session:
    """Resolve a query to exactly one session, or exit with error."""
    conn = get_connection()
    matches = find_session(conn, query)
    if len(matches) == 0:
        console.print(f"[red]No session matching:[/red] {query}", highlight=False)
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(
            f"[yellow]Ambiguous — {len(matches)} sessions match '{query}':[/yellow]",
            highlight=False,
        )
        for s in matches[:10]:
            pid = str(s.pid) if s.pid else _short_id(s.id)
            console.print(
                f"  {pid:>8}  {s.label or '—':16}  {_compact_cwd(s.cwd) if s.cwd else '—'}",
                highlight=False,
            )
        raise typer.Exit(1)
    return matches[0]


def _group_by_cwd(sessions: list[Session]) -> list[tuple[str, list[Session]]]:
    """Group sessions by CWD, sorted: groups with waiting first, then by most recent."""
    from collections import OrderedDict

    groups: dict[str, list[Session]] = OrderedDict()
    for s in sessions:
        key = _compact_cwd(s.cwd) if s.cwd else "unknown"
        groups.setdefault(key, []).append(s)

    # Within each group: waiting first, then by updated_at desc.
    for key in groups:
        groups[key].sort(
            key=lambda s: (
                0 if s.state == SessionState.WAITING_FOR_USER else 1,
                s.updated_at or "",
            ),
        )

    # Sort groups: any group with a waiting session comes first, then by most recent update.
    def group_sort_key(item: tuple[str, list[Session]]) -> tuple[int, str]:
        _cwd, sess = item
        has_waiting = any(s.state == SessionState.WAITING_FOR_USER for s in sess)
        latest = max((s.updated_at or "" for s in sess), default="")
        return (0 if has_waiting else 1, latest)

    return sorted(groups.items(), key=group_sort_key)


def _print_table(sessions: list[Session]) -> None:
    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        return

    total = len(sessions)
    n_waiting = sum(1 for s in sessions if s.state == SessionState.WAITING_FOR_USER)
    n_running = sum(1 for s in sessions if s.state == SessionState.RUNNING)
    n_other = total - n_waiting - n_running
    projects = len({s.cwd for s in sessions})

    parts = [f"[bold]{total}[/bold] sessions"]
    if n_waiting:
        parts.append(f"[bold yellow]{n_waiting} waiting[/bold yellow]")
    if n_running:
        parts.append(f"[green]{n_running} running[/green]")
    if n_other:
        parts.append(f"[dim]{n_other} other[/dim]")
    parts.append(f"{projects} projects")

    console.print(f"─── {' · '.join(parts)} ───")
    console.print()

    grouped = _group_by_cwd(sessions)

    for i, (cwd, group) in enumerate(grouped):
        if i > 0:
            console.print()

        console.print(f"[bold]{cwd}[/bold]")

        table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 1, 0, 2))
        table.add_column("PID", style="cyan", no_wrap=True)
        table.add_column("LABEL", max_width=45, overflow="ellipsis")
        table.add_column("STATE", no_wrap=True)
        table.add_column("UPDATED", justify="right", no_wrap=True)
        table.add_column("TMUX", style="dim", no_wrap=True, justify="right")

        for s in group:
            stale = is_stale(s)
            tmux = f"{s.tmux_session}:{s.tmux_window}" if s.tmux_session else "—"
            pid = str(s.pid) if s.pid else "—"
            label = s.label or "—"
            updated = _relative_time(s.updated_at)

            row_style = ""
            if s.state in ("done", "archived") or stale:
                row_style = "dim"

            table.add_row(
                pid,
                label,
                _state_text(s.state, stale),
                updated,
                tmux,
                style=row_style,
            )

        console.print(table)


def _print_detail(s: Session) -> None:
    stale = is_stale(s)
    state_display = _state_text(s.state, stale)

    console.print()
    console.print(f"  [bold]id[/bold]           {s.id}")
    console.print(f"  [bold]label[/bold]        {s.label or '[dim]—[/dim]'}")
    console.print("  [bold]state[/bold]        ", end="")
    console.print(state_display)
    console.print(f"  [bold]host[/bold]         {s.host or '[dim]—[/dim]'}")
    console.print(f"  [bold]cwd[/bold]          {_compact_cwd(s.cwd) if s.cwd else '[dim]—[/dim]'}")
    console.print(f"  [bold]repo_root[/bold]    {s.repo_root or '[dim]—[/dim]'}")
    console.print(f"  [bold]branch[/bold]       {s.branch or '[dim]—[/dim]'}")
    tmux = f"{s.tmux_session or '—'}:{s.tmux_window or '—'}:{s.tmux_pane or '—'}"
    console.print(f"  [bold]tmux[/bold]         {tmux}")
    console.print(f"  [bold]pid[/bold]          {s.pid or '[dim]—[/dim]'}")
    console.print(f"  [bold]managed[/bold]      {'yes' if s.is_managed else 'no'}")
    console.print(f"  [bold]summary[/bold]      {s.summary or '[dim]—[/dim]'}")
    console.print(f"  [bold]created[/bold]      {_relative_time(s.created_at)}")
    console.print(f"  [bold]updated[/bold]      {_relative_time(s.updated_at)}")
    console.print(f"  [bold]last_seen[/bold]    {_relative_time(s.last_seen_at)}")
    console.print(
        f"  [bold]archived_at[/bold]  {s.archived_at or '[dim]—[/dim]'}"
    )


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

    # Ensure the Telegram bot is running (if configured).
    from agent_interface.telegram import ensure_bot_running
    try:
        ensure_bot_running()
    except Exception:
        pass  # Best-effort.

    if ctx.invoked_subcommand is None:
        cmd_list(all=False)


@app.command("list")
def cmd_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include done and archived sessions."),
) -> None:
    """List active sessions."""
    conn = get_connection()
    sessions = list_sessions(conn, include_done=all, include_archived=all)
    _print_table(sessions)


@app.command("waiting")
def cmd_waiting() -> None:
    """Show sessions waiting for user input."""
    conn = get_connection()
    sessions = list_waiting(conn)
    _print_table(sessions)


@app.command("show")
def cmd_show(query: str) -> None:
    """Show full details for a session. Matches against id, label, cwd, or pid."""
    s = _resolve(query)
    _print_detail(s)

    conn = get_connection()
    events = list_events(conn, s.id)
    if events:
        console.print(f"\n  [bold]Events ({len(events)}):[/bold]")
        for e in events:
            console.print(
                f"    [dim]{_relative_time(e.created_at):>8}[/dim]  {e.event_type}"
                f"  [dim]{e.payload_json or ''}[/dim]"
            )
    console.print()


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
        console.print(f"[red]Invalid state:[/red] {state}")
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
    console.print(f"Registered session: [cyan]{session_id}[/cyan]")


@app.command("update-state")
def cmd_update_state(query: str, state: str) -> None:
    """Update the state of a session."""
    try:
        SessionState(state)
    except ValueError:
        valid = ", ".join(s.value for s in SessionState)
        console.print(f"[red]Invalid state:[/red] {state}\nValid: {valid}")
        raise typer.Exit(1)

    s = _resolve(query)
    conn = get_connection()
    update_state(conn, s.id, state)
    console.print(f"State updated: [cyan]{_short_id(s.id)}[/cyan] → {state}")


@app.command("rename")
def cmd_rename(query: str, label: str) -> None:
    """Rename a session."""
    s = _resolve(query)
    conn = get_connection()
    rename_session(conn, s.id, label)
    console.print(f"Renamed: [cyan]{_short_id(s.id)}[/cyan] → {label}")


@app.command("label")
def cmd_label(label: str) -> None:
    """Set label for the current session (auto-detects by process tree)."""
    import os

    from agent_interface.hooks import _find_by_pid_ancestry

    conn = get_connection()
    match = _find_by_pid_ancestry(conn, os.getpid())
    if match is None:
        console.print("[red]Could not find a session for this process.[/red]", highlight=False)
        raise typer.Exit(1)
    rename_session(conn, match.id, label)
    console.print(f"Labeled: [cyan]{_short_id(match.id)}[/cyan] → {label}")


@app.command("archive")
def cmd_archive(query: str) -> None:
    """Archive a session."""
    s = _resolve(query)
    conn = get_connection()
    archive_session(conn, s.id)
    console.print(f"Archived: [cyan]{_short_id(s.id)}[/cyan]")


@app.command("prune")
def cmd_prune() -> None:
    """Archive all stale and done sessions."""
    conn = get_connection()
    sessions = list_sessions(conn, include_done=True)
    pruned = 0
    for s in sessions:
        if s.state == SessionState.DONE or is_stale(s):
            archive_session(conn, s.id)
            pruned += 1
    if pruned:
        console.print(f"Pruned {pruned} session(s).")
    else:
        console.print("[dim]Nothing to prune.[/dim]")


@app.command("restore")
def cmd_restore(query: str) -> None:
    """Restore an archived session."""
    s = _resolve(query)
    conn = get_connection()
    restore_session(conn, s.id)
    console.print(f"Restored: [cyan]{_short_id(s.id)}[/cyan]")


@app.command("jump")
def cmd_jump(query: str) -> None:
    """Jump to a session's tmux pane."""
    import shutil
    import subprocess

    s = _resolve(query)

    if not s.tmux_session:
        console.print("[red]No tmux metadata for this session.[/red]", highlight=False)
        raise typer.Exit(1)

    if not shutil.which("tmux"):
        console.print("[red]tmux not found.[/red]", highlight=False)
        raise typer.Exit(1)

    target = f"{s.tmux_session}:{s.tmux_window}.{s.tmux_pane}"

    # Detect if we're inside tmux.
    in_tmux = os.environ.get("TMUX")

    if in_tmux:
        subprocess.run(["tmux", "switch-client", "-t", target], check=False)
    else:
        subprocess.run(["tmux", "attach", "-t", target], check=False)

    # Zoom only if not already zoomed.
    zoomed = subprocess.run(
        ["tmux", "display-message", "-t", target, "-p", "#{window_zoomed_flag}"],
        capture_output=True, text=True, check=False,
    )
    if zoomed.stdout.strip() != "1":
        subprocess.run(["tmux", "resize-pane", "-Z", "-t", target], check=False)


@app.command("hook", hidden=True)
def cmd_hook() -> None:
    """Process a hook event from stdin (called by agent hooks)."""
    from agent_interface.hooks import read_and_process_stdin

    result = read_and_process_stdin()
    if result.startswith("error:"):
        typer.echo(result, err=True)
        raise typer.Exit(1)


@app.command("init-hooks")
def cmd_init_hooks() -> None:
    """Install agi hooks into ~/.claude/settings.json."""
    from agent_interface.hooks import install_hooks

    ok, msg = install_hooks()
    console.print(msg)
    if not ok:
        raise typer.Exit(1)

    # Register Telegram bot commands if configured.
    try:
        from agent_interface.telegram import register_commands
        register_commands()
    except Exception:
        pass


@app.command("scan")
def cmd_scan() -> None:
    """Scan for running agent sessions and register new ones."""
    from agent_interface.scan import scan_and_register

    hooks_ok, results = scan_and_register()

    if hooks_ok:
        console.print("[green]Hooks installed[/green] into ~/.claude/settings.json")

    if not results:
        console.print("[dim]No agent sessions found.[/dim]")
        return

    registered = [(a, p) for a, p in results if a == "registered"]
    skipped = [(a, p) for a, p in results if a == "skipped"]

    for _, proc in registered:
        tmux = ""
        if proc.tmux_session:
            tmux = f" [dim](tmux {proc.tmux_session}:{proc.tmux_window}.{proc.tmux_pane})[/dim]"
        cwd = _compact_cwd(proc.cwd) if proc.cwd else "?"
        console.print(f"  [green]+[/green] [cyan]{proc.pid}[/cyan]  {cwd}{tmux}")

    if registered:
        console.print(f"\nRegistered {len(registered)} new session(s).")
    if skipped:
        console.print(f"[dim]Skipped {len(skipped)} already tracked.[/dim]")
    if not registered and skipped:
        console.print("[dim]All found sessions are already tracked.[/dim]")


@app.command("bot")
def cmd_bot(
    foreground: bool = typer.Option(False, "--fg", help="Run in foreground."),
) -> None:
    """Start the Telegram bot."""
    from agent_interface.telegram import (
        _bot_pid_alive,
        ensure_bot_running,
        poll_and_reply,
        send_message,
    )

    if foreground:
        console.print("Starting Telegram bot in foreground... (Ctrl+C to stop)")
        try:
            send_message("agi bot started. Send /help for commands.")
            poll_and_reply()
        except KeyboardInterrupt:
            console.print("\nBot stopped.")
        return

    if _bot_pid_alive():
        console.print("[dim]Bot is already running.[/dim]")
        return

    ensure_bot_running()
    console.print("[green]Bot started in background.[/green]")


@app.command("bot-stop")
def cmd_bot_stop() -> None:
    """Stop the background Telegram bot."""
    from agent_interface.telegram import stop_bot

    if stop_bot():
        console.print("Bot stopped.")
    else:
        console.print("[dim]Bot is not running.[/dim]")


@app.command("notify-test")
def cmd_notify_test() -> None:
    """Send a test notification to Telegram."""
    from agent_interface.telegram import send_message

    ok = send_message("Test notification from agi.")
    if ok:
        console.print("[green]Notification sent.[/green]")
    else:
        console.print("[red]Failed. Check ~/.config/agi/config.json[/red]")


@app.command("dashboard")
def cmd_dashboard() -> None:
    """Update the pinned Telegram dashboard."""
    from agent_interface.telegram import (
        _load_dashboard_state,
        _save_dashboard_state,
        update_dashboard,
    )
    state = _load_dashboard_state()
    state["last_updated"] = 0
    _save_dashboard_state(state)

    if update_dashboard():
        console.print("[green]Dashboard updated.[/green]")
    else:
        console.print("[red]Failed. Check Telegram config.[/red]")


def main() -> None:
    app()
