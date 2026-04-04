"""Scan for running coding agent sessions and register them."""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent_interface.db import get_connection
from agent_interface.models import Session
from agent_interface.registry import get_session, register_session


@dataclass
class ProcessInfo:
    pid: int
    cwd: Optional[str]
    cmdline: str
    tmux_session: Optional[str] = None
    tmux_window: Optional[str] = None
    tmux_pane: Optional[str] = None


def find_claude_processes() -> list[ProcessInfo]:
    """Find running processes that look like coding agent sessions."""
    results: list[ProcessInfo] = []

    try:
        # Find PIDs whose process name is exactly "claude".
        out = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return results

    if out.returncode != 0:
        return results

    my_pid = os.getpid()
    my_ppid = os.getppid()

    for line in out.stdout.strip().splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue

        # Skip ourselves and our parent (the shell running agi).
        if pid in (my_pid, my_ppid):
            continue

        info = _read_process_info(pid)
        if info and _looks_like_agent(info):
            results.append(info)

    return results


def _read_process_info(pid: int) -> Optional[ProcessInfo]:
    """Read process metadata from /proc."""
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return None

    # Read cmdline.
    try:
        cmdline = (proc / "cmdline").read_text().replace("\x00", " ").strip()
    except OSError:
        return None

    if not cmdline:
        return None

    # Read cwd.
    cwd = None
    try:
        cwd = str((proc / "cwd").resolve())
    except OSError:
        pass

    return ProcessInfo(pid=pid, cwd=cwd, cmdline=cmdline)


def _looks_like_agent(info: ProcessInfo) -> bool:
    """Filter out non-agent processes that happen to match 'claude'."""
    cmd = info.cmdline.lower()

    # Skip ourselves (agi commands).
    if "agi " in cmd or "agent_interface" in cmd:
        return False

    # Skip editors, grep, etc. that might reference claude in args.
    skip_prefixes = ("grep", "pgrep", "rg ", "vim", "nano", "less", "cat", "tail")
    for prefix in skip_prefixes:
        if cmd.startswith(prefix):
            return False

    # Skip background/internal claude processes (not interactive sessions).
    if info.cwd and ".claude/" in info.cwd:
        return False

    return True


def _get_tmux_pane_pids() -> dict[int, tuple[str, str, str]]:
    """Map pane PIDs to (session_name, window_index, pane_index)."""
    pane_map: dict[int, tuple[str, str, str]] = {}
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_pid} #{session_name} #{window_index} #{pane_index}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return pane_map

    if out.returncode != 0:
        return pane_map

    for line in out.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            try:
                pane_pid = int(parts[0])
                pane_map[pane_pid] = (parts[1], parts[2], parts[3])
            except ValueError:
                continue

    return pane_map


def _get_parent_pid(pid: int) -> Optional[int]:
    """Get the parent PID from /proc."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # Format: pid (comm) state ppid ...
        # Find closing paren to handle spaces in comm.
        close_paren = stat.rfind(")")
        fields = stat[close_paren + 2:].split()
        return int(fields[1])  # ppid is field index 1 after state
    except (OSError, IndexError, ValueError):
        return None


def _find_tmux_context(
    pid: int, pane_map: dict[int, tuple[str, str, str]],
) -> Optional[tuple[str, str, str]]:
    """Walk up the process tree to find if this process runs inside a tmux pane."""
    visited: set[int] = set()
    current = pid
    while current and current > 1 and current not in visited:
        visited.add(current)
        if current in pane_map:
            return pane_map[current]
        current = _get_parent_pid(current)
    return None


def enrich_with_tmux(processes: list[ProcessInfo]) -> None:
    """Add tmux metadata to processes if they're running inside tmux."""
    pane_map = _get_tmux_pane_pids()
    if not pane_map:
        return

    for proc in processes:
        ctx = _find_tmux_context(proc.pid, pane_map)
        if ctx:
            proc.tmux_session, proc.tmux_window, proc.tmux_pane = ctx


def scan_and_register() -> tuple[bool, list[tuple[str, ProcessInfo]]]:
    """Scan for agent processes, install hooks, and register new ones.

    Returns (hooks_installed, list of (action, process_info)).
    """
    from agent_interface.hooks import install_hooks

    hooks_installed, hooks_msg = install_hooks()

    processes = find_claude_processes()
    enrich_with_tmux(processes)

    conn = get_connection()
    hostname = socket.gethostname()
    results: list[tuple[str, ProcessInfo]] = []

    # Check which PIDs are already tracked.
    existing_pids: set[int] = set()
    rows = conn.execute("SELECT pid FROM sessions WHERE pid IS NOT NULL").fetchall()
    for row in rows:
        existing_pids.add(row["pid"])

    for proc in processes:
        if proc.pid in existing_pids:
            results.append(("skipped", proc))
            continue

        session = Session(
            id=f"{hostname}:{proc.pid}",
            state="running",
            host=hostname,
            cwd=proc.cwd,
            pid=proc.pid,
            tmux_session=proc.tmux_session,
            tmux_window=proc.tmux_window,
            tmux_pane=proc.tmux_pane,
        )

        # Check if ID already exists (e.g., from a previous scan).
        if get_session(conn, session.id) is not None:
            results.append(("skipped", proc))
            continue

        register_session(conn, session)
        results.append(("registered", proc))

    return hooks_installed, results
