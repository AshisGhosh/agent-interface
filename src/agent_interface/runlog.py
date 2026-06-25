"""Per-project command runbook — journal and replay the commands agents run.

Agents working across projects (sim evals, nav pipelines, remote batch runs)
repeatedly need the *exact* command they ran last time — but that knowledge
dies with the session. This records each command an agent runs through
``agi run`` (the command, where it ran, exit code, duration, output tail) keyed
by project, so a later session can recall the history with ``agi runs`` or
replay a named command with ``agi run --replay <name>``.

It is intentionally project-scoped: the key is the git repo root (falling back
to the cwd), so the runbook is the same whether you invoke it from the repo
root or a subdirectory, and runs from different projects never mix.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    name        TEXT,
    cmd         TEXT NOT NULL,
    cwd         TEXT NOT NULL,
    exit_code   INTEGER,
    duration_s  REAL,
    output_tail TEXT,
    started_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_command_runs_project ON command_runs(project);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def project_key(cwd: Optional[str] = None) -> str:
    """Stable key for the project containing *cwd*.

    Uses the git toplevel so the runbook is shared across subdirectories of a
    repo; falls back to the absolute cwd for non-git directories.
    """
    path = Path(cwd).expanduser() if cwd else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return str(path.resolve())


def record_run(
    conn,
    *,
    project: str,
    cmd: str,
    cwd: str,
    exit_code: Optional[int],
    duration_s: float,
    output_tail: str = "",
    name: Optional[str] = None,
    started_at: Optional[float] = None,
) -> int:
    """Persist one command run and return its row id."""
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO command_runs "
        "(project, name, cmd, cwd, exit_code, duration_s, output_tail, started_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            project,
            name,
            cmd,
            cwd,
            exit_code,
            duration_s,
            output_tail,
            time.time() if started_at is None else started_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_runs(
    conn,
    project: str,
    *,
    limit: int = 20,
    name: Optional[str] = None,
) -> list:
    """Most-recent-first runs for *project* (optionally filtered by name)."""
    _ensure(conn)
    if name is not None:
        rows = conn.execute(
            "SELECT * FROM command_runs WHERE project=? AND name=? "
            "ORDER BY started_at DESC LIMIT ?",
            (project, name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM command_runs WHERE project=? "
            "ORDER BY started_at DESC LIMIT ?",
            (project, limit),
        ).fetchall()
    return list(rows)


def last_run(conn, project: str, *, name: Optional[str] = None):
    """The most recent run for *project* (optionally by name), or None."""
    rows = list_runs(conn, project, limit=1, name=name)
    return rows[0] if rows else None


def build_command(parts: list[str]) -> str:
    """Reconstruct a shell command string from CLI argument *parts*.

    A single argument is used verbatim so shell syntax (pipes, &&, redirects)
    passed as one quoted string survives; multiple bare arguments are quoted
    and joined so an ordinary ``python eval.py --scene x`` round-trips safely.
    """
    import shlex

    if len(parts) == 1:
        return parts[0]
    return shlex.join(parts)


def run_command(cmd: str, cwd: str, *, tail_lines: int = 40, stream=None):
    """Execute *cmd* via the shell, streaming output and capturing the tail.

    Returns ``(exit_code, duration_s, output_tail)``. *stream*, if given, is a
    callable invoked with each output line (already newline-terminated) for
    live display; output is always captured into a bounded tail regardless.
    """
    from collections import deque

    tail: deque[str] = deque(maxlen=tail_lines)
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as e:
        return (127, time.time() - start, f"failed to launch: {e}")

    assert proc.stdout is not None
    for line in proc.stdout:
        tail.append(line)
        if stream is not None:
            stream(line)
    proc.wait()
    return (proc.returncode, time.time() - start, "".join(tail))
