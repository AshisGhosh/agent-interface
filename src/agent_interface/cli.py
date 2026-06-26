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
        table.add_column("LABEL", max_width=40, overflow="ellipsis")
        table.add_column("STATE", no_wrap=True)
        table.add_column("ACTIVITY", style="dim", no_wrap=True, max_width=25)
        table.add_column("UPDATED", justify="right", no_wrap=True)
        table.add_column("TMUX", style="dim", no_wrap=True, justify="right")

        for s in group:
            stale = is_stale(s)
            tmux = f"{s.tmux_session}:{s.tmux_window}" if s.tmux_session else "—"
            pid = str(s.pid) if s.pid else "—"
            label = s.label or "—"
            updated = _relative_time(s.updated_at)

            # Activity: show last tool + count for running sessions.
            activity = ""
            if s.state == "running" and s.last_tool:
                activity = f"{s.last_tool} ({s.tool_count})"
            elif s.state == "running" and s.tool_count:
                activity = f"{s.tool_count} tools"

            row_style = ""
            if s.state in ("done", "archived") or stale:
                row_style = "dim"

            table.add_row(
                pid,
                label,
                _state_text(s.state, stale),
                activity,
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
    tool_info = f"{s.last_tool} ({s.tool_count} calls)" if s.last_tool else f"{s.tool_count} calls"
    console.print(f"  [bold]tools[/bold]        {tool_info}")
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


@app.command("doctor")
def cmd_doctor() -> None:
    """Reconcile the registry against live processes (reap stale/phantom sessions)."""
    from agent_interface.registry import reconcile

    conn = get_connection()
    summary = reconcile(conn)
    reaped = summary["reaped_exited"] + summary["reaped_reused"] + summary["reaped_pidless"]
    console.print(
        f"Checked [cyan]{summary['checked']}[/cyan] session(s); "
        f"reaped [yellow]{reaped}[/yellow] "
        f"([dim]{summary['reaped_exited']} exited, "
        f"{summary['reaped_reused']} pid-reused, "
        f"{summary['reaped_pidless']} pidless-stale[/dim])."
    )


def _hooks_install_due(interval_seconds: int = 3600) -> bool:
    """True if hooks haven't been reinstalled within `interval_seconds`.

    Records the timestamp on each affirmative so the heartbeat only pays the
    `claude mcp` reinstall cost ~hourly instead of every tick.
    """
    import time
    from pathlib import Path

    stamp = Path.home() / ".config" / "agi" / "hooks_last_install"
    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < interval_seconds:
        return False
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(time.time()))
    except OSError:
        pass
    return True


@app.command("heartbeat", hidden=True)
def cmd_heartbeat() -> None:
    """Idempotent self-heal tick: scan, reconcile, keep the bot + dashboard alive.

    Safe to run repeatedly (cron / supervisor). Never raises.
    """
    from agent_interface.registry import reconcile

    steps: list[str] = []
    try:
        from agent_interface.scan import scan_and_register
        # Reinstalling hooks every tick spawns `claude mcp` subprocesses for no
        # benefit. Throttle to ~hourly so hooks still self-heal if removed.
        do_hooks = _hooks_install_due()
        scan_and_register(install_hooks=do_hooks)
        steps.append("scan+hooks" if do_hooks else "scan")
    except Exception as e:  # noqa: BLE001
        steps.append(f"scan!{type(e).__name__}")

    try:
        conn = get_connection()
        summary = reconcile(conn)
        reaped = summary["reaped_exited"] + summary["reaped_reused"] + summary["reaped_pidless"]
        steps.append(f"reconcile(reaped={reaped})")
    except Exception as e:  # noqa: BLE001
        steps.append(f"reconcile!{type(e).__name__}")

    try:
        from agent_interface.telegram import ensure_bot_running, update_dashboard
        ensure_bot_running()
        update_dashboard()
        steps.append("bot+dashboard")
    except Exception as e:  # noqa: BLE001
        steps.append(f"bot!{type(e).__name__}")

    try:
        from agent_interface.optimizer import deliver_pending, maybe_run
        # Land any stranded improvements first, then consider a new dispatch.
        delivered = deliver_pending()
        result = maybe_run()
        tags = []
        if delivered.get("landed"):
            tags.append(f"landed={len(delivered['landed'])}")
        if result.get("dispatched"):
            tags.append("dispatched")
        steps.append("optimize" + ("(" + ",".join(tags) + ")" if tags else ""))
    except Exception as e:  # noqa: BLE001
        steps.append(f"optimize!{type(e).__name__}")

    try:
        from agent_interface import features
        verdict = features.evaluate(get_connection())
        if verdict["used"] or verdict["failed"]:
            steps.append(f"features(used={len(verdict['used'])},failed={len(verdict['failed'])})")
    except Exception as e:  # noqa: BLE001
        steps.append(f"features!{type(e).__name__}")

    console.print(f"heartbeat: {' · '.join(steps)}")


usage_app = typer.Typer(help="Feature-usage ledger (records whether shipped features get used).")
app.add_typer(usage_app, name="usage")


@usage_app.command("record")
def cmd_usage_record(
    feature_id: str,
    source: Optional[str] = typer.Option(None, "--source", help="Where the use came from."),
) -> None:
    """Record one use of a shipped feature (called by the feature itself)."""
    from agent_interface.usage import record_usage

    record_usage(feature_id, source=source)


@app.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
def cmd_run(
    cmd: Optional[list[str]] = typer.Argument(
        None, help="Command to run (e.g. python eval.py --scene nfinite)."
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Tag this run so it can be replayed by name."
    ),
    replay: Optional[str] = typer.Option(
        None, "--replay", help="Re-run the most recent run with this name."
    ),
    last: bool = typer.Option(
        False, "--last", help="Re-run the most recent command in this project."
    ),
    tail: int = typer.Option(40, "--tail", help="Output lines to keep in the journal."),
) -> None:
    """Run a command and journal it in this project's runbook.

    Records the exact command, its exit code, duration, and output tail keyed by
    the project (git root, else cwd) so a later session can recall it with
    `agi runs` or replay it with `agi run --replay <name>` / `agi run --last`.
    Works from any project directory.
    """
    from agent_interface.runlog import (
        build_command,
        last_run,
        project_key,
        record_run,
        run_command,
    )
    from agent_interface.usage import record_usage

    record_usage("feat-b0126e38", source="run")

    cwd = os.getcwd()
    project = project_key(cwd)
    conn = get_connection()

    # Resolve the command: replay a prior one, or build it from the arguments.
    if replay is not None or last:
        prior = last_run(conn, project, name=replay)
        if prior is None:
            target = f"named '{replay}'" if replay else "any"
            console.print(
                f"[red]No prior run ({target}) recorded for this project.[/red]",
                highlight=False,
            )
            raise typer.Exit(1)
        command = prior["cmd"]
        if name is None:
            name = prior["name"]
        console.print(f"[dim]replaying:[/dim] {command}", highlight=False)
    else:
        if not cmd:
            console.print(
                "[red]No command given.[/red] Pass a command, or use "
                "--last / --replay <name>.",
                highlight=False,
            )
            raise typer.Exit(1)
        command = build_command(list(cmd))

    label = f" [dim]({name})[/dim]" if name else ""
    console.print(f"[bold green]▶[/bold green] {command}{label}", highlight=False)

    exit_code, duration_s, output_tail = run_command(
        command, cwd, tail_lines=tail, stream=lambda line: console.file.write(line)
    )

    record_run(
        conn,
        project=project,
        cmd=command,
        cwd=cwd,
        exit_code=exit_code,
        duration_s=duration_s,
        output_tail=output_tail,
        name=name,
    )

    status = "[green]ok[/green]" if exit_code == 0 else f"[red]exit {exit_code}[/red]"
    console.print(
        f"[dim]──[/dim] {status} [dim]in {duration_s:.1f}s · journaled to "
        f"{_compact_cwd(project)}[/dim]",
        highlight=False,
    )
    if exit_code != 0:
        raise typer.Exit(exit_code or 1)


@app.command("runs")
def cmd_runs(
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Only show runs tagged with this name."
    ),
    limit: int = typer.Option(20, "--limit", help="Max runs to show."),
) -> None:
    """Show this project's command runbook (recent `agi run` invocations)."""
    from agent_interface.runlog import list_runs, project_key

    project = project_key(os.getcwd())
    conn = get_connection()
    runs = list_runs(conn, project, limit=limit, name=name)
    if not runs:
        console.print(
            f"[dim]No runs recorded for {_compact_cwd(project)} yet. "
            "Run one with `agi run <cmd>`.[/dim]"
        )
        return

    console.print(f"[bold]{_compact_cwd(project)}[/bold] runbook")
    table = Table(show_header=True, box=None, pad_edge=False, padding=(0, 1, 0, 2))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("WHEN", style="dim", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("TOOK", justify="right", no_wrap=True, style="dim")
    table.add_column("NAME", style="magenta", no_wrap=True)
    table.add_column("COMMAND", overflow="ellipsis", max_width=60)

    for r in runs:
        ec = r["exit_code"]
        if ec is None:
            status = Text("?", style="dim")
        elif ec == 0:
            status = Text("ok", style="green")
        else:
            status = Text(f"exit {ec}", style="red")
        when = _relative_time(
            datetime.fromtimestamp(r["started_at"], tz=timezone.utc).isoformat()
        )
        took = f"{r['duration_s']:.1f}s" if r["duration_s"] is not None else "—"
        table.add_row(
            str(r["id"]), when, status, took, r["name"] or "—", r["cmd"]
        )
    console.print(table)


@app.command("flake")
def cmd_flake(
    test: Optional[list[str]] = typer.Argument(
        None, help="Test/scenario name (e.g. block-rel-test or sim_fast::loop_back)."
    ),
    status: str = typer.Option(
        "fail", "--status", "-s", help="Outcome: pass / fail (synonyms ok)."
    ),
    note: Optional[str] = typer.Option(
        None, "--note", "-m", help="Optional context (branch, host, what changed)."
    ),
    ms: Optional[float] = typer.Option(
        None, "--ms", help="Duration of the run in milliseconds."
    ),
) -> None:
    """Record one test outcome in this project's flaky-test ledger.

    Keyed by the project (git root, else cwd) so the history follows the repo, not
    the session. Read the picture back with `agi flakes` to tell a genuinely
    flaky test (passes sometimes) apart from a deterministic failure before
    sinking a session into "investigating" it. Works from any project directory.
    """
    from agent_interface.flake import normalize_status, project_key, record_result
    from agent_interface.usage import record_usage

    record_usage("feat-6103e6d0", source="flake")

    if not test:
        console.print(
            "[red]No test name given.[/red] e.g. "
            "`agi flake block-rel-test -s fail -m 'motor stalled'`.",
            highlight=False,
        )
        raise typer.Exit(1)

    name = " ".join(test)
    try:
        canon = normalize_status(status)
    except ValueError as e:
        console.print(f"[red]{e}[/red]", highlight=False)
        raise typer.Exit(1)

    project = project_key(os.getcwd())
    conn = get_connection()
    record_result(
        conn, project=project, test=name, status=canon, note=note, duration_ms=ms
    )

    mark = "[green]pass[/green]" if canon == "pass" else "[red]fail[/red]"
    console.print(
        f"recorded {mark} for [bold]{name}[/bold] "
        f"[dim]({_compact_cwd(project)})[/dim]",
        highlight=False,
    )


@app.command("flakes")
def cmd_flakes(
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Only show tests whose name contains this text."
    ),
    flaky_only: bool = typer.Option(
        False, "--flaky", help="Show only flaky tests (hide stable pass/fail)."
    ),
) -> None:
    """Show this project's flaky-test report (outcomes from `agi flake`)."""
    from agent_interface.flake import flaky_stats, project_key
    from agent_interface.usage import record_usage

    record_usage("feat-6103e6d0", source="flakes")

    project = project_key(os.getcwd())
    conn = get_connection()
    stats = flaky_stats(conn, project, name=name, flaky_only=flaky_only)
    if not stats:
        console.print(
            f"[dim]No test results for {_compact_cwd(project)} yet. "
            "Record one with `agi flake <test> -s fail`.[/dim]"
        )
        return

    console.print(f"[bold]{_compact_cwd(project)}[/bold] flaky-test report")
    table = Table(show_header=True, box=None, pad_edge=False, padding=(0, 1, 0, 2))
    table.add_column("TEST", overflow="ellipsis", max_width=46)
    table.add_column("KIND", no_wrap=True)
    table.add_column("PASS", justify="right", no_wrap=True, style="green")
    table.add_column("FAIL", justify="right", no_wrap=True, style="red")
    table.add_column("FAIL%", justify="right", no_wrap=True)
    table.add_column("LAST", no_wrap=True)
    table.add_column("SEEN", style="dim", no_wrap=True)

    kind_style = {"flaky": "yellow", "failing": "red", "passing": "green"}
    for s in stats:
        kind = Text(s["kind"], style=kind_style.get(s["kind"], "dim"))
        last = Text(
            s["last_status"] or "—",
            style="green" if s["last_status"] == "pass" else "red",
        )
        seen = (
            _relative_time(
                datetime.fromtimestamp(s["last_seen"], tz=timezone.utc).isoformat()
            )
            if s["last_seen"]
            else "—"
        )
        table.add_row(
            s["test"],
            kind,
            str(s["passes"]),
            str(s["fails"]),
            f"{s['fail_rate'] * 100:.0f}%",
            last,
            seen,
        )
    console.print(table)


@app.command("note")
def cmd_note(
    text: Optional[list[str]] = typer.Argument(
        None, help="The note to leave for the next agent in this project."
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Optional label to group/filter the note."
    ),
) -> None:
    """Leave a note in this project's notebook for the next session.

    Captures freeform knowledge — a gotcha, a decision, a "try X not Y" hint —
    keyed by the project (git root, else cwd) so the next agent can read it back
    with `agi notes`. Distinct from `agi run`, which journals commands.
    Works from any project directory.
    """
    from agent_interface.notes import add_note, project_key
    from agent_interface.usage import record_usage

    record_usage("feat-49daff52", source="note")

    body = " ".join(text).strip() if text else ""
    if not body:
        console.print(
            "[red]No note given.[/red] Pass the note text, e.g. "
            '`agi note "build needs node 18"`.',
            highlight=False,
        )
        raise typer.Exit(1)

    project = project_key(os.getcwd())
    conn = get_connection()
    note_id = add_note(conn, project=project, note=body, tag=tag)
    label = f" [dim]#{tag}[/dim]" if tag else ""
    console.print(
        f"[bold green]✎[/bold green] noted{label} [dim](#{note_id} · "
        f"{_compact_cwd(project)})[/dim]",
        highlight=False,
    )


@app.command("notes")
def cmd_notes(
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Only show notes with this tag."
    ),
    search: Optional[str] = typer.Option(
        None, "--search", "-s", help="Only show notes matching this text."
    ),
    limit: int = typer.Option(50, "--limit", help="Max notes to show."),
    rm: Optional[int] = typer.Option(
        None, "--rm", help="Delete the note with this id from this project."
    ),
) -> None:
    """Read back this project's notebook (notes left via `agi note`)."""
    from agent_interface.notes import list_notes, project_key, remove_note
    from agent_interface.usage import record_usage

    record_usage("feat-49daff52", source="notes")

    project = project_key(os.getcwd())
    conn = get_connection()

    if rm is not None:
        if remove_note(conn, project, rm):
            console.print(f"[dim]removed note #{rm}[/dim]")
        else:
            console.print(
                f"[red]No note #{rm} in {_compact_cwd(project)}.[/red]",
                highlight=False,
            )
            raise typer.Exit(1)
        return

    notes = list_notes(conn, project, tag=tag, query=search, limit=limit)
    if not notes:
        console.print(
            f"[dim]No notes for {_compact_cwd(project)} yet. "
            'Leave one with `agi note "<text>"`.[/dim]'
        )
        return

    console.print(f"[bold]{_compact_cwd(project)}[/bold] notebook")
    for n in notes:
        when = _relative_time(
            datetime.fromtimestamp(n["created_at"], tz=timezone.utc).isoformat()
        )
        tag_label = f" [magenta]#{n['tag']}[/magenta]" if n["tag"] else ""
        console.print(
            f"  [cyan]#{n['id']}[/cyan]{tag_label} [dim]{when}[/dim]\n"
            f"    {n['note']}",
            highlight=False,
        )


def _fmt_metric_value(value: Optional[float]) -> str:
    """Render a metric value compactly: ints as ints, floats trimmed."""
    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


@app.command("finding")
def cmd_finding(
    label: Optional[list[str]] = typer.Argument(
        None, help="Variant/experiment name, e.g. 'v3-distance-pred'."
    ),
    metric: Optional[str] = typer.Option(
        None, "--metric", "-m", help="Metric name, e.g. 'val_loss' or 'success_rate'."
    ),
    value: Optional[float] = typer.Option(
        None, "--value", "-v", help="Numeric value for the metric."
    ),
    note: Optional[str] = typer.Option(
        None, "--note", "-n", help="Optional freeform context for this result."
    ),
) -> None:
    """Log an experiment result for this project's findings ledger.

    Captures a labeled variant's result — an optional metric/value pair plus a
    note — keyed by the project (git root, else cwd) so a later session can read
    it back with `agi findings` or rank variants with `agi findings --compare`.
    Distinct from `agi note` (prose) and `agi run` (commands). Works from any
    project directory.
    """
    from agent_interface.findings import project_key, record_finding
    from agent_interface.usage import record_usage

    record_usage("feat-27d124a5", source="finding")

    name = " ".join(label).strip() if label else ""
    if not name:
        console.print(
            "[red]No label given.[/red] Name the variant, e.g. "
            '`agi finding v3-distance-pred --metric val_loss --value 0.23`.',
            highlight=False,
        )
        raise typer.Exit(1)
    if value is not None and metric is None:
        console.print(
            "[red]--value needs --metric.[/red] Name the metric the value is for, "
            "e.g. `--metric val_loss --value 0.23`.",
            highlight=False,
        )
        raise typer.Exit(1)

    project = project_key(os.getcwd())
    conn = get_connection()
    fid = record_finding(
        conn, project=project, label=name, metric=metric, value=value, note=note
    )

    detail = ""
    if metric is not None:
        detail = f" [cyan]{metric}[/cyan]=[bold]{_fmt_metric_value(value)}[/bold]"
    console.print(
        f"[bold green]✦[/bold green] logged [magenta]{name}[/magenta]{detail} "
        f"[dim](#{fid} · {_compact_cwd(project)})[/dim]",
        highlight=False,
    )


@app.command("findings")
def cmd_findings(
    metric: Optional[str] = typer.Option(
        None, "--metric", "-m", help="Only show findings for this metric."
    ),
    label: Optional[str] = typer.Option(
        None, "--label", "-l", help="Only show findings for this variant."
    ),
    compare: bool = typer.Option(
        False, "--compare", "-c",
        help="Rank variants by their best value for --metric (best first).",
    ),
    minimize: bool = typer.Option(
        False, "--min",
        help="With --compare, treat lower values as better (e.g. loss).",
    ),
    limit: int = typer.Option(50, "--limit", help="Max findings to show."),
    rm: Optional[int] = typer.Option(
        None, "--rm", help="Delete the finding with this id from this project."
    ),
) -> None:
    """Read back this project's findings ledger (logged via `agi finding`)."""
    from agent_interface.findings import (
        compare_findings,
        list_findings,
        project_key,
        remove_finding,
    )
    from agent_interface.usage import record_usage

    record_usage("feat-27d124a5", source="findings")

    project = project_key(os.getcwd())
    conn = get_connection()

    if rm is not None:
        if remove_finding(conn, project, rm):
            console.print(f"[dim]removed finding #{rm}[/dim]")
        else:
            console.print(
                f"[red]No finding #{rm} in {_compact_cwd(project)}.[/red]",
                highlight=False,
            )
            raise typer.Exit(1)
        return

    if compare:
        if metric is None:
            console.print(
                "[red]--compare needs --metric.[/red] Pick the metric to rank by, "
                "e.g. `agi findings --compare --metric success_rate`.",
                highlight=False,
            )
            raise typer.Exit(1)
        ranked = compare_findings(
            conn, project, metric, higher_is_better=not minimize
        )
        if not ranked:
            console.print(
                f"[dim]No '{metric}' values logged for "
                f"{_compact_cwd(project)} yet.[/dim]"
            )
            return
        order = "lower is better" if minimize else "higher is better"
        table = Table(
            title=f"{_compact_cwd(project)} · {metric} ({order})",
            show_edge=False,
        )
        table.add_column("#", justify="right", style="dim")
        table.add_column("variant", style="magenta")
        table.add_column(metric, justify="right")
        table.add_column("runs", justify="right", style="dim")
        for rank, e in enumerate(ranked, 1):
            marker = "[bold green]★[/bold green]" if rank == 1 else str(rank)
            table.add_row(
                marker,
                e["label"],
                _fmt_metric_value(e["best"]),
                str(e["runs"]),
            )
        console.print(table)
        return

    findings = list_findings(conn, project, metric=metric, label=label, limit=limit)
    if not findings:
        console.print(
            f"[dim]No findings for {_compact_cwd(project)} yet. "
            'Log one with `agi finding <variant> --metric <m> --value <v>`.[/dim]'
        )
        return

    console.print(f"[bold]{_compact_cwd(project)}[/bold] findings")
    for f in findings:
        when = _relative_time(
            datetime.fromtimestamp(f["created_at"], tz=timezone.utc).isoformat()
        )
        result = ""
        if f["metric"] is not None:
            result = (
                f" [cyan]{f['metric']}[/cyan]="
                f"[bold]{_fmt_metric_value(f['value'])}[/bold]"
            )
        note = f"\n    {f['note']}" if f["note"] else ""
        console.print(
            f"  [dim]#{f['id']}[/dim] [magenta]{f['label']}[/magenta]{result} "
            f"[dim]{when}[/dim]{note}",
            highlight=False,
        )


scaffold_app = typer.Typer(
    help="Reusable code-scaffold library (save a template once, stamp it out anywhere)."
)
app.add_typer(scaffold_app, name="scaffold")


@scaffold_app.command("save")
def cmd_scaffold_save(
    name: str = typer.Argument(..., help="Scaffold name, e.g. 'react-component'."),
    file: Optional[str] = typer.Option(
        None, "--file", "-f", help="Read the template body from this file."
    ),
    body: Optional[str] = typer.Option(
        None, "--body", "-b", help="Template body inline (else --file, else stdin)."
    ),
    desc: Optional[str] = typer.Option(
        None, "--desc", "-d", help="One-line description of what this scaffolds."
    ),
    project: bool = typer.Option(
        False, "--project", "-p",
        help="Scope to this project (git root, else cwd) instead of global.",
    ),
) -> None:
    """Save a reusable code template, with `{{placeholder}}` holes, by name.

    Body comes from `--body`, else `--file`, else stdin. Saved global by default
    so it travels across every project; `--project` scopes it to this repo and
    shadows a global of the same name. Re-saving a name overwrites it. Stamp a
    filled copy out later with `agi scaffold new <name> <dest> --var k=v`.
    """
    import sys
    from pathlib import Path

    from agent_interface.scaffold import placeholders, project_key, save_scaffold
    from agent_interface.usage import record_usage

    record_usage("feat-572904ee", source="scaffold-save")

    if body is not None:
        text = body
    elif file is not None:
        try:
            text = Path(file).expanduser().read_text()
        except OSError as e:
            console.print(f"[red]Could not read {file}:[/red] {e}", highlight=False)
            raise typer.Exit(1)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        console.print(
            "[red]No template body.[/red] Pass --body, --file, or pipe via stdin, "
            "e.g. `agi scaffold save react-component --file Button.tsx`.",
            highlight=False,
        )
        raise typer.Exit(1)

    if not text.strip():
        console.print("[red]Refusing to save an empty scaffold.[/red]")
        raise typer.Exit(1)

    scope = project_key(os.getcwd()) if project else "global"
    conn = get_connection()
    _id, created = save_scaffold(
        conn, name=name, body=text, scope=scope, description=desc
    )

    holes = placeholders(text)
    where = _compact_cwd(scope) if project else "global"
    verb = "saved" if created else "updated"
    hole_str = ""
    if holes:
        hole_str = " · holes: " + ", ".join(f"[cyan]{h}[/cyan]" for h in holes)
    console.print(
        f"[bold green]✦[/bold green] {verb} scaffold [magenta]{name}[/magenta] "
        f"[dim]({where})[/dim]{hole_str}",
        highlight=False,
    )


@scaffold_app.command("list")
def cmd_scaffold_list() -> None:
    """List scaffolds available here — globals plus this project's own."""
    from agent_interface.scaffold import list_scaffolds, placeholders, project_key
    from agent_interface.usage import record_usage

    record_usage("feat-572904ee", source="scaffold-list")

    project = project_key(os.getcwd())
    conn = get_connection()
    rows = list_scaffolds(conn, project=project)
    if not rows:
        console.print(
            "[dim]No scaffolds yet. Save one with "
            "`agi scaffold save <name> --file <path>`.[/dim]"
        )
        return

    table = Table(show_edge=False)
    table.add_column("name", style="magenta")
    table.add_column("scope", style="dim")
    table.add_column("holes", style="cyan")
    table.add_column("description")
    for r in rows:
        scope = "global" if r["scope"] == "global" else _compact_cwd(r["scope"])
        holes = ", ".join(placeholders(r["body"])) or "—"
        table.add_row(r["name"], scope, holes, r["description"] or "—")
    console.print(table)


@scaffold_app.command("show")
def cmd_scaffold_show(
    name: str = typer.Argument(..., help="Scaffold name to print."),
) -> None:
    """Print a scaffold's raw template body and its placeholders."""
    from agent_interface.scaffold import get_scaffold, placeholders, project_key
    from agent_interface.usage import record_usage

    record_usage("feat-572904ee", source="scaffold-show")

    project = project_key(os.getcwd())
    conn = get_connection()
    row = get_scaffold(conn, name, project=project)
    if row is None:
        console.print(
            f"[red]No scaffold named '{name}'.[/red] See `agi scaffold list`.",
            highlight=False,
        )
        raise typer.Exit(1)

    holes = placeholders(row["body"])
    hole_str = ", ".join(holes) if holes else "none"
    scope = "global" if row["scope"] == "global" else _compact_cwd(row["scope"])
    console.print(
        f"[bold]{name}[/bold] [dim]({scope})[/dim] · holes: [cyan]{hole_str}[/cyan]",
        highlight=False,
    )
    if row["description"]:
        console.print(f"[dim]{row['description']}[/dim]", highlight=False)
    console.print(row["body"], highlight=False, markup=False)


@scaffold_app.command("new")
def cmd_scaffold_new(
    name: str = typer.Argument(..., help="Scaffold name to render."),
    dest: Optional[str] = typer.Argument(
        None, help="Output file path; omit to print to stdout."
    ),
    var: Optional[list[str]] = typer.Option(
        None, "--var", "-v", help="Fill a placeholder: key=value (repeatable)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite dest if it already exists."
    ),
) -> None:
    """Render a scaffold, filling `{{placeholder}}` holes, to a file or stdout.

    Provide values with repeated `--var key=value`. Unfilled holes are left
    verbatim and reported so a partial render is still obviously a template.
    """
    from pathlib import Path

    from agent_interface.scaffold import (
        get_scaffold,
        parse_var,
        project_key,
        render,
    )
    from agent_interface.usage import record_usage

    record_usage("feat-572904ee", source="scaffold-new")

    project = project_key(os.getcwd())
    conn = get_connection()
    row = get_scaffold(conn, name, project=project)
    if row is None:
        console.print(
            f"[red]No scaffold named '{name}'.[/red] See `agi scaffold list`.",
            highlight=False,
        )
        raise typer.Exit(1)

    variables: dict[str, str] = {}
    for item in var or []:
        try:
            key, value = parse_var(item)
        except ValueError:
            console.print(
                f"[red]Bad --var {item!r}.[/red] Use key=value, e.g. "
                "`--var name=SpellBar`.",
                highlight=False,
            )
            raise typer.Exit(1)
        variables[key] = value

    rendered, missing = render(row["body"], variables)

    if dest is not None:
        out = Path(dest).expanduser()
        if out.exists() and not force:
            console.print(
                f"[red]{dest} already exists.[/red] Pass --force to overwrite.",
                highlight=False,
            )
            raise typer.Exit(1)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered)
        except OSError as e:
            console.print(f"[red]Could not write {dest}:[/red] {e}", highlight=False)
            raise typer.Exit(1)
        console.print(
            f"[bold green]✦[/bold green] wrote [magenta]{name}[/magenta] → "
            f"[bold]{dest}[/bold]",
            highlight=False,
        )
    else:
        # Rendered template is the payload — print raw so it can be piped.
        print(rendered, end="" if rendered.endswith("\n") else "\n")

    if missing:
        console.print(
            "[yellow]unfilled holes:[/yellow] "
            + ", ".join(f"[cyan]{m}[/cyan]" for m in missing)
            + " [dim](pass with --var)[/dim]",
            highlight=False,
        )


@scaffold_app.command("rm")
def cmd_scaffold_rm(
    name: str = typer.Argument(..., help="Scaffold name to delete."),
    project: bool = typer.Option(
        False, "--project", "-p",
        help="Delete this project's scoped scaffold instead of the global one.",
    ),
) -> None:
    """Delete a scaffold by name (global by default, `--project` for project scope)."""
    from agent_interface.scaffold import project_key, remove_scaffold
    from agent_interface.usage import record_usage

    record_usage("feat-572904ee", source="scaffold-rm")

    scope = project_key(os.getcwd()) if project else "global"
    conn = get_connection()
    if remove_scaffold(conn, name, scope=scope):
        where = _compact_cwd(scope) if project else "global"
        console.print(f"[dim]removed scaffold '{name}' ({where})[/dim]")
    else:
        console.print(
            f"[red]No scaffold named '{name}' in that scope.[/red]",
            highlight=False,
        )
        raise typer.Exit(1)


def _fmt_scores(scores: list) -> str:
    """Render an ordered ``[(criterion, score), ...]`` list inline."""
    return "  ".join(
        f"[cyan]{name}[/cyan]=[bold]{_fmt_metric_value(score)}[/bold]"
        for name, score in scores
    )


@app.command("assess")
def cmd_assess(
    subject: Optional[list[str]] = typer.Argument(
        None, help="What is being iterated on, e.g. 'art-overhaul'."
    ),
    criterion: Optional[list[str]] = typer.Option(
        None, "--criterion", "-c",
        help="A rubric score as name=value (repeatable), e.g. -c lighting=7.",
    ),
    verdict: Optional[str] = typer.Option(
        None, "--verdict", "-V",
        help="Short call on this iteration, e.g. 'ship' / 'needs-work'.",
    ),
    note: Optional[str] = typer.Option(
        None, "--note", "-n", help="Freeform context for this iteration."
    ),
) -> None:
    """Score the current iteration of a subject against rubric criteria.

    Captures a qualitative eval of where this iteration landed — per-criterion
    scores plus an optional verdict/note — keyed by the project (git root, else
    cwd). The iteration number is assigned automatically. Read the history and
    per-criterion trend back with `agi assessments <subject> --trend`. Distinct
    from `agi finding`, which ranks variants on one metric. Works from any
    project directory.
    """
    from agent_interface.assess import parse_criterion, project_key, record_assessment
    from agent_interface.usage import record_usage

    record_usage("feat-3a801c34", source="assess")

    name = " ".join(subject).strip() if subject else ""
    if not name:
        console.print(
            "[red]No subject given.[/red] Name what you're iterating on, e.g. "
            '`agi assess art-overhaul -c lighting=7 -c palette=5`.',
            highlight=False,
        )
        raise typer.Exit(1)

    scores: list = []
    for raw in criterion or []:
        try:
            scores.append(parse_criterion(raw))
        except ValueError as e:
            console.print(f"[red]{e}[/red]", highlight=False)
            raise typer.Exit(1) from None

    if not scores and verdict is None and note is None:
        console.print(
            "[red]Nothing to assess.[/red] Give at least one criterion, a "
            "--verdict, or a --note.",
            highlight=False,
        )
        raise typer.Exit(1)

    project = project_key(os.getcwd())
    conn = get_connection()
    result = record_assessment(
        conn,
        project=project,
        subject=name,
        scores=scores,
        verdict=verdict,
        note=note,
    )

    detail = f"  {_fmt_scores(result['scores'])}" if result["scores"] else ""
    verdict_label = f" [yellow]{verdict}[/yellow]" if verdict else ""
    console.print(
        f"[bold green]◎[/bold green] assessed [magenta]{name}[/magenta] "
        f"[bold]#iter {result['iteration']}[/bold]{verdict_label}{detail} "
        f"[dim](#{result['id']} · {_compact_cwd(project)})[/dim]",
        highlight=False,
    )


@app.command("assessments")
def cmd_assessments(
    subject: Optional[list[str]] = typer.Argument(
        None, help="Only show assessments for this subject."
    ),
    trend: bool = typer.Option(
        False, "--trend", "-t",
        help="Show the per-criterion trend across iterations (needs a subject).",
    ),
    limit: int = typer.Option(50, "--limit", help="Max assessments to show."),
    rm: Optional[int] = typer.Option(
        None, "--rm", help="Delete the assessment with this id from this project."
    ),
) -> None:
    """Read back this project's assessments (logged via `agi assess`)."""
    from agent_interface.assess import (
        assessment_trend,
        list_assessments,
        project_key,
        remove_assessment,
    )
    from agent_interface.usage import record_usage

    record_usage("feat-3a801c34", source="assessments")

    project = project_key(os.getcwd())
    conn = get_connection()

    if rm is not None:
        if remove_assessment(conn, project, rm):
            console.print(f"[dim]removed assessment #{rm}[/dim]")
        else:
            console.print(
                f"[red]No assessment #{rm} in {_compact_cwd(project)}.[/red]",
                highlight=False,
            )
            raise typer.Exit(1)
        return

    subj = " ".join(subject).strip() if subject else ""

    if trend:
        if not subj:
            console.print(
                "[red]--trend needs a subject.[/red] Pick the subject to chart, "
                "e.g. `agi assessments art-overhaul --trend`.",
                highlight=False,
            )
            raise typer.Exit(1)
        rows = assessment_trend(conn, project, subj)
        if not rows:
            console.print(
                f"[dim]No assessments of '{subj}' for "
                f"{_compact_cwd(project)} yet.[/dim]"
            )
            return
        arrows = {
            "up": "[green]↑[/green]",
            "down": "[red]↓[/red]",
            "flat": "[dim]→[/dim]",
        }
        console.print(f"[bold]{_compact_cwd(project)}[/bold] · {subj} trend")
        for e in rows:
            series = " ".join(
                f"[dim]i{it}:[/dim]{_fmt_metric_value(sc)}" for it, sc in e["points"]
            )
            delta = e["delta"]
            sign = "+" if delta > 0 else ""
            console.print(
                f"  [cyan]{e['criterion']}[/cyan] {arrows[e['direction']]} "
                f"[dim]({sign}{_fmt_metric_value(delta)})[/dim]  {series}",
                highlight=False,
            )
        return

    assessments = list_assessments(
        conn, project, subject=subj or None, limit=limit
    )
    if not assessments:
        scope = f"'{subj}' in " if subj else ""
        console.print(
            f"[dim]No assessments for {scope}{_compact_cwd(project)} yet. "
            'Log one with `agi assess <subject> -c <name>=<score>`.[/dim]'
        )
        return

    console.print(f"[bold]{_compact_cwd(project)}[/bold] assessments")
    for a in assessments:
        when = _relative_time(
            datetime.fromtimestamp(a["created_at"], tz=timezone.utc).isoformat()
        )
        verdict_label = f" [yellow]{a['verdict']}[/yellow]" if a["verdict"] else ""
        scores = f"\n    {_fmt_scores(a['scores'])}" if a["scores"] else ""
        note = f"\n    [dim]{a['note']}[/dim]" if a["note"] else ""
        console.print(
            f"  [dim]#{a['id']}[/dim] [magenta]{a['subject']}[/magenta] "
            f"[bold]i{a['iteration']}[/bold]{verdict_label} [dim]{when}[/dim]"
            f"{scores}{note}",
            highlight=False,
        )


@app.command("job")
def cmd_job(
    title: Optional[list[str]] = typer.Argument(
        None, help="What this job is (e.g. 'H100 sweep: polargrad lr=3e-4')."
    ),
    job_id: Optional[str] = typer.Option(
        None, "--id", "-i", help="Cluster/SLURM job id returned at submission."
    ),
    aim: Optional[str] = typer.Option(
        None, "--aim", "-a", help="AIM run hash or remote streaming URL to watch."
    ),
    status: Optional[str] = typer.Option(
        None, "--status", "-s",
        help="Job status: submitted, running, done, failed, cancelled.",
    ),
    note: Optional[str] = typer.Option(
        None, "--note", "-n", help="Freeform note (config, caveat, what to check)."
    ),
    update: Optional[int] = typer.Option(
        None, "--update", "-u", help="Update the tracked job with this id instead of adding."
    ),
) -> None:
    """Track a cluster/remote job (id + AIM streaming URL) for the next session.

    Record a job when you submit it so a later agent — or you after a context
    reset — can recall its cluster id and AIM run with `agi jobs` instead of
    re-submitting or losing the stream:

        agi job "H100 sweep: polargrad" --id 481923 --aim https://aim/run/ab12

    Re-use the same command with `--update <n>` to bump status or attach an AIM
    run once the job starts. Project-scoped; works from any project directory.
    """
    from agent_interface.jobs import STATUSES, add_job, get_job, project_key, update_job
    from agent_interface.usage import record_usage

    record_usage("feat-c7780666", source="job")

    if status is not None and status not in STATUSES:
        console.print(
            f"[red]Invalid status '{status}'.[/red] Use one of: {', '.join(STATUSES)}.",
            highlight=False,
        )
        raise typer.Exit(1)

    body = " ".join(title).strip() if title else ""
    project = project_key(os.getcwd())
    conn = get_connection()

    if update is not None:
        # Title is optional on update — only patch what was passed.
        patched = update_job(
            conn,
            project,
            update,
            job_id=job_id,
            aim=aim,
            status=status,
            note=note or (body or None),
        )
        if not patched:
            existing = get_job(conn, project, update)
            if existing is None:
                console.print(
                    f"[red]No job #{update} in {_compact_cwd(project)}.[/red]",
                    highlight=False,
                )
            else:
                console.print(
                    "[red]Nothing to update.[/red] Pass --status/--id/--aim/--note.",
                    highlight=False,
                )
            raise typer.Exit(1)
        row = get_job(conn, project, update)
        console.print(
            f"[bold green]⟳[/bold green] job [cyan]#{update}[/cyan] "
            f"[yellow]{row['status']}[/yellow] [dim]({_compact_cwd(project)})[/dim]",
            highlight=False,
        )
        return

    if not body:
        console.print(
            "[red]No job title given.[/red] Pass a description, e.g. "
            '`agi job "H100 sweep" --id 481923 --aim <run>`.',
            highlight=False,
        )
        raise typer.Exit(1)

    row_id = add_job(
        conn,
        project=project,
        title=body,
        job_id=job_id,
        aim=aim,
        status=status or "submitted",
        note=note,
    )
    extras = []
    if job_id:
        extras.append(f"id {job_id}")
    if aim:
        extras.append(f"aim {aim}")
    suffix = f" [dim]({' · '.join(extras)})[/dim]" if extras else ""
    console.print(
        f"[bold green]☁[/bold green] tracked job [cyan]#{row_id}[/cyan] "
        f"[yellow]{status or 'submitted'}[/yellow]{suffix} "
        f"[dim]({_compact_cwd(project)})[/dim]",
        highlight=False,
    )


@app.command("jobs")
def cmd_jobs(
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help="Only show jobs with this status."
    ),
    open_only: bool = typer.Option(
        False, "--open", help="Only show in-flight jobs (submitted/running)."
    ),
    limit: int = typer.Option(50, "--limit", help="Max jobs to show."),
    rm: Optional[int] = typer.Option(
        None, "--rm", help="Delete the tracked job with this id from this project."
    ),
) -> None:
    """List this project's tracked cluster/remote jobs (recorded via `agi job`)."""
    from agent_interface.jobs import list_jobs, project_key, remove_job
    from agent_interface.usage import record_usage

    record_usage("feat-c7780666", source="jobs")

    project = project_key(os.getcwd())
    conn = get_connection()

    if rm is not None:
        if remove_job(conn, project, rm):
            console.print(f"[dim]removed job #{rm}[/dim]")
        else:
            console.print(
                f"[red]No job #{rm} in {_compact_cwd(project)}.[/red]",
                highlight=False,
            )
            raise typer.Exit(1)
        return

    jobs = list_jobs(conn, project, status=status, open_only=open_only, limit=limit)
    if not jobs:
        console.print(
            f"[dim]No jobs tracked for {_compact_cwd(project)} yet. "
            'Record one with `agi job "<title>" --id <cluster-id>`.[/dim]'
        )
        return

    status_styles = {
        "submitted": "yellow",
        "running": "green",
        "done": "dim",
        "failed": "red",
        "cancelled": "dim red",
    }
    console.print(f"[bold]{_compact_cwd(project)}[/bold] jobs")
    table = Table(show_header=True, box=None, pad_edge=False, padding=(0, 1, 0, 2))
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("WHEN", style="dim", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("JOB", style="magenta", no_wrap=True)
    table.add_column("AIM", overflow="ellipsis", max_width=34, style="blue")
    table.add_column("TITLE", overflow="ellipsis", max_width=44)

    for j in jobs:
        when = _relative_time(
            datetime.fromtimestamp(j["updated_at"], tz=timezone.utc).isoformat()
        )
        st = Text(j["status"], style=status_styles.get(j["status"], "white"))
        table.add_row(
            str(j["id"]),
            when,
            st,
            j["job_id"] or "—",
            j["aim"] or "—",
            j["title"],
        )
    console.print(table)


@app.command("features")
def cmd_features() -> None:
    """Show autonomously-shipped features and whether they've been used."""
    from agent_interface.features import list_features
    from agent_interface.usage import usage_count

    feats = list_features()
    if not feats:
        console.print("[dim]No features shipped by the optimizer yet.[/dim]")
        return
    conn = get_connection()
    for f in feats:
        uses = usage_count(conn, f["id"], since=f.get("shipped_at"))
        color = {"used": "green", "failed": "red", "shipped": "yellow"}.get(f["status"], "dim")
        console.print(
            f"  [{color}]{f['status']}[/]  {f['title'][:50]}  "
            f"[dim]{uses} uses · helps {_compact_cwd(f.get('helps') or '?')}[/dim]"
        )


@app.command("up", context_settings={"ignore_unknown_options": True})
def cmd_up(
    cmd: Optional[list[str]] = typer.Argument(None, help="Command to run (omit to list daemons)."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Name for this daemon."),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory."),
    all_projects: bool = typer.Option(False, "--all", "-a", help="List across all projects."),
) -> None:
    """Launch a durable, detached background process (dashboard, server, watcher).

    The process survives the current turn/session (started via setsid, output
    to a log). agi never reaps it. With no command, lists tracked daemons.

      agi up npm run dev --name dash     # launch, detached + logged
      agi up                             # list this project's daemons
      agi down dash                      # stop it
    """
    from agent_interface import daemon

    if not cmd:
        rows = daemon.list_daemons(cwd=cwd, all_projects=all_projects)
        if not rows:
            console.print("[dim]No daemons. Launch one: agi up <cmd> --name <name>[/dim]")
            return
        for r in rows:
            color = "green" if r["status"] == "running" else "dim"
            console.print(
                f"  [{color}]{r['status']:8}[/] [cyan]{r['name']}[/cyan] "
                f"pid={r['pid']}  [dim]{r['cmd'][:40]}[/dim]"
            )
            console.print(f"           [dim]log: {r['log_path']}[/dim]")
        return

    try:
        info = daemon.launch(cmd, name=name, cwd=cwd)
    except ValueError as e:
        console.print(f"[red]{e}[/red]", highlight=False)
        raise typer.Exit(1)
    console.print(
        f"[green]up[/green] [cyan]{info['name']}[/cyan] (pid {info['pid']}) — "
        "detached, survives this session."
    )
    console.print(f"  [dim]log: {info['log_path']}[/dim]  ·  stop with: agi down {info['name']}")


@app.command("down")
def cmd_down(
    name: str,
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory."),
) -> None:
    """Stop a daemon started with `agi up`."""
    from agent_interface import daemon

    if daemon.stop(name, cwd=cwd):
        console.print(f"[green]down[/green] {name} (SIGTERM sent).")
    else:
        console.print(f"[dim]{name}: not running (marked stopped).[/dim]")


@app.command("insights")
def cmd_insights(
    min_sessions: int = typer.Option(3, "--min", help="Min sessions per project to qualify."),
) -> None:
    """Show recurring agent workflows mined from session history."""
    from agent_interface.insights import analyze_sessions

    conn = get_connection()
    opps = analyze_sessions(conn, min_sessions=min_sessions)
    if not opps:
        console.print("[dim]Not enough labelled session history yet.[/dim]")
        return

    for o in opps:
        kw = ", ".join(f"{k}×{c}" for k, c in o.keywords[:5])
        console.print(
            f"[bold]{_compact_cwd(o.repo)}[/bold]  "
            f"[cyan]{o.session_count}[/cyan] sessions  "
            f"[dim](score {o.score:.1f})[/dim]"
        )
        console.print(f"    [dim]{kw}[/dim]")


optimize_app = typer.Typer(help="Autonomous self-improvement loop.")
app.add_typer(optimize_app, name="optimize")


@optimize_app.command("status")
def cmd_optimize_status() -> None:
    """Show optimizer config, guardrail state, and recent dispatches."""
    import time

    from agent_interface.optimizer import (
        KILLSWITCH_PATH,
        _config,
        load_state,
        should_dispatch,
    )

    cfg = _config()
    state = load_state()
    decision = should_dispatch(state, cfg, time.time(), killswitch=KILLSWITCH_PATH.exists())
    console.print(f"  enabled:        {cfg['enabled']}")
    console.print(f"  kill-switch:    {'PRESENT' if KILLSWITCH_PATH.exists() else 'off'}")
    console.print(f"  daily cap:      {cfg['max_dispatches_per_day']}")
    console.print(f"  min interval:   {cfg['min_interval_seconds']}s")
    day = state.get("day") or "—"
    console.print(f"  dispatched today: {state.get('dispatches_today', 0)} (day {day})")
    console.print(f"  next action:    [{'green' if decision.ok else 'yellow'}]{decision.reason}[/]")


@optimize_app.command("enable")
def cmd_optimize_enable() -> None:
    """Turn the autonomous loop on (writes config.optimizer.enabled=true)."""
    _set_optimizer_enabled(True)
    console.print("[green]Optimizer enabled.[/green] Disable instantly with: agi optimize kill")


@optimize_app.command("disable")
def cmd_optimize_disable() -> None:
    """Turn the autonomous loop off."""
    _set_optimizer_enabled(False)
    console.print("Optimizer disabled.")


@optimize_app.command("kill")
def cmd_optimize_kill() -> None:
    """Drop the kill-switch file — hard-stops the loop immediately."""
    from agent_interface.optimizer import KILLSWITCH_PATH

    KILLSWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILLSWITCH_PATH.write_text("disabled by `agi optimize kill`\n")
    console.print(f"[red]Kill-switch set.[/red] Remove {KILLSWITCH_PATH} to resume.")


@optimize_app.command("deliveries")
def cmd_optimize_deliveries(
    land: bool = typer.Option(
        False, "--land", help="Land fast-forwardable, test-passing ones now.",
    ),
) -> None:
    """Show (or land) completed improvements not yet merged into main."""
    from agent_interface.optimizer import _default_repo, deliver_pending, pending_deliveries

    if land:
        res = deliver_pending(notify=False)
        for i in res["landed"]:
            console.print(f"  [green]landed[/green] {i['title'][:60]}")
        for i in res["flagged"]:
            console.print(f"  [yellow]{i['reason']}[/yellow] {i['title'][:50]} [{i['branch']}]")
        if not res["landed"] and not res["flagged"]:
            console.print("[dim]Nothing pending.[/dim]")
        return

    from agent_interface.orchestrator.db import get_connection as _oc
    pend = pending_deliveries(_oc(), _default_repo())
    if not pend:
        console.print("[dim]All completed improvements are merged into main.[/dim]")
        return
    for i in pend:
        console.print(f"  [yellow]pending[/yellow] {i['title'][:60]} [{i['branch']}]")


@optimize_app.command("run")
def cmd_optimize_run() -> None:
    """Run one optimizer tick now (respects all guardrails)."""
    from agent_interface.optimizer import maybe_run

    result = maybe_run()
    if result.get("dispatched"):
        console.print(
            f"[green]Dispatched[/green] task {result['task_id']} "
            f"for {_compact_cwd(result['repo'])}."
        )
    else:
        console.print(f"[dim]No dispatch: {result.get('reason')}[/dim]")


def _set_optimizer_enabled(value: bool) -> None:
    import json

    from agent_interface.telegram import CONFIG_PATH

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}
    cfg.setdefault("optimizer", {})["enabled"] = value
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


supervisor_app = typer.Typer(help="Keep agi up across crashes and reboots (systemd).")
app.add_typer(supervisor_app, name="supervisor")


@supervisor_app.command("install")
def cmd_supervisor_install() -> None:
    """Install systemd user units (bot + heartbeat timer) and enable lingering."""
    from agent_interface import supervisor

    ok, log = supervisor.install()
    for line in log:
        console.print(f"  {line}")
    console.print("[green]Supervisor installed.[/green]" if ok else "[red]Install failed.[/red]")


@supervisor_app.command("status")
def cmd_supervisor_status() -> None:
    """Show the state of the managed systemd units."""
    from agent_interface import supervisor

    for unit, state in supervisor.status().items():
        color = "green" if state == "active" else "yellow"
        console.print(f"  {unit}: [{color}]{state}[/]")


@supervisor_app.command("uninstall")
def cmd_supervisor_uninstall() -> None:
    """Remove the systemd user units."""
    from agent_interface import supervisor

    ok, log = supervisor.uninstall()
    for line in log:
        console.print(f"  {line}")
    console.print("Supervisor removed." if ok else "[red]Uninstall failed.[/red]")


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
        # Claim the pidfile so heartbeat's ensure_bot_running() sees this bot
        # (e.g. when systemd owns it) and never spawns a second long-poller —
        # Telegram getUpdates allows only one, a duplicate causes 409 conflicts.
        import os as _os

        from agent_interface.telegram import PIDFILE_PATH
        PIDFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PIDFILE_PATH.write_text(str(_os.getpid()))
        try:
            send_message("agi bot started. Send /help for commands.")
            poll_and_reply()
        except KeyboardInterrupt:
            console.print("\nBot stopped.")
        finally:
            PIDFILE_PATH.unlink(missing_ok=True)
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


# ── orchestrator integration ─────────────────────────────────────────────────

from agent_interface.orchestrator import cli as _orch_cli  # noqa: E402

app.add_typer(_orch_cli.projects_app, name="projects")
app.add_typer(_orch_cli.tasks_app, name="tasks")
app.command("next")(_orch_cli.cmd_next)
app.command("progress")(_orch_cli.cmd_progress)
app.command("block")(_orch_cli.cmd_block)
app.command("unblock")(_orch_cli.cmd_unblock)
app.command("done")(_orch_cli.cmd_done)
app.command("board")(_orch_cli.cmd_board)
app.command("dispatch")(_orch_cli.cmd_dispatch)
app.command("review")(_orch_cli.cmd_review)
app.command("approve")(_orch_cli.cmd_approve)
app.command("reject")(_orch_cli.cmd_reject)
app.command("watch")(_orch_cli.cmd_watch)


@app.command("mcp", hidden=True)
def cmd_mcp() -> None:
    """Run the orchestrator MCP server on stdio (agent-facing)."""
    from agent_interface.orchestrator.mcp_server import run

    run()


@app.command("serve")
def cmd_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    port: int = typer.Option(8000, "--port", help="FastAPI port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Also start the Next.js dev server; skip serving the static export.",
    ),
    ui_port: int = typer.Option(3000, "--ui-port", help="Next.js dev-server port."),
    ui_dir: str = typer.Option(
        "ui",
        "--ui-dir",
        help="Path to the Next.js project (relative to cwd or absolute).",
    ),
    static_dir: Optional[str] = typer.Option(
        None,
        "--static-dir",
        help="Override the path to the Next.js static export (defaults to <ui-dir>/out).",
    ),
    no_ui: bool = typer.Option(
        False, "--no-ui", help="Run only the API; don't mount or spawn any UI.",
    ),
) -> None:
    """Run the FastAPI backend, optionally with the Next.js dev server.

    Three modes:

      agi serve --dev   → FastAPI (reload) + `npm run dev` in ui/. The dev
                          server proxies /api/* to FastAPI. Browse the UI
                          on http://localhost:<ui-port>.
      agi serve         → FastAPI serves the built Next.js static export
                          (ui/out) at /. Run `npm run build` in ui/ first.
      agi serve --no-ui → API only, no static mount, no child process.
    """
    import uvicorn

    from agent_interface.web import create_app

    if dev:
        _serve_dev(host=host, port=port, ui_dir=ui_dir, ui_port=ui_port, reload=reload)
        return

    resolved_static: Optional[str] = None
    if not no_ui:
        resolved_static = _resolve_static_dir(ui_dir, static_dir)

    if reload:
        if resolved_static is not None:
            os.environ["AGI_STATIC_DIR"] = resolved_static
        else:
            os.environ.pop("AGI_STATIC_DIR", None)
        uvicorn.run(
            "agent_interface.web:create_app_from_env",
            host=host,
            port=port,
            reload=True,
            reload_excludes=[".worktrees/*", "ui/*", ".venv/*", "*.pyc"],
            factory=True,
        )
    else:
        uvicorn.run(create_app(static_dir=resolved_static), host=host, port=port)


def _resolve_static_dir(ui_dir: str, override: Optional[str]) -> Optional[str]:
    """Return an existing static-export directory, or None with a warning."""
    from pathlib import Path

    candidate = Path(override) if override else Path(ui_dir) / "out"
    candidate = candidate.expanduser().resolve()
    if candidate.is_dir():
        return str(candidate)
    console.print(
        f"[yellow]No static export at[/yellow] {candidate}. "
        "Run `npm run build` in the UI directory, or pass --dev / --no-ui.",
        highlight=False,
    )
    return None


def _serve_dev(*, host: str, port: int, ui_dir: str, ui_port: int, reload: bool) -> None:
    """Run FastAPI (optionally with --reload) alongside `npm run dev`."""
    import shutil
    import signal
    import subprocess
    from pathlib import Path

    import uvicorn

    from agent_interface.web import create_app

    ui_path = Path(ui_dir).expanduser().resolve()
    if not (ui_path / "package.json").is_file():
        console.print(
            f"[red]No package.json at[/red] {ui_path}. "
            "Pass --ui-dir or run from the repo root.",
            highlight=False,
        )
        raise typer.Exit(1)

    npm = shutil.which("npm")
    if npm is None:
        console.print("[red]npm not found on PATH.[/red]", highlight=False)
        raise typer.Exit(1)

    env = os.environ.copy()
    env["AGI_API_URL"] = f"http://{host}:{port}"

    console.print(
        f"[green]Starting Next.js dev server[/green] in {ui_path} "
        f"(port {ui_port}); API at http://{host}:{port}",
        highlight=False,
    )

    ui_proc = subprocess.Popen(
        [npm, "run", "dev", "--",
         "--hostname", host, "--port", str(ui_port)],
        cwd=str(ui_path),
        env=env,
    )

    def _stop_ui(*_: object) -> None:
        if ui_proc.poll() is None:
            ui_proc.terminate()

    signal.signal(signal.SIGTERM, lambda *_: _stop_ui())

    try:
        if reload:
            os.environ.pop("AGI_STATIC_DIR", None)
            uvicorn.run(
                "agent_interface.web:create_app_from_env",
                host=host,
                port=port,
                reload=True,
                reload_excludes=[".worktrees/*", "ui/*", ".venv/*", "*.pyc"],
                factory=True,
            )
        else:
            uvicorn.run(create_app(), host=host, port=port)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_ui()
        try:
            ui_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ui_proc.kill()


def main() -> None:
    app()
