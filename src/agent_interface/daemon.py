"""Durable background processes — `agi up` / `agi down`.

Agents working in a project often need to launch something long-running (a dev
server, a dashboard, a watcher) and have it OUTLIVE the turn/session that
started it. A naive `cmd &` dies when the launching shell exits. This launches
the command fully detached (its own session via setsid) with output to a log
file, and records it in a small per-project ledger so a later session can see
what's running, read its log, and stop it.

Nothing here is reaped by agi: this is a plain detached process the registry
never touches. The ledger just remembers it.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

from agent_interface.db import get_connection
from agent_interface.models import _now_utc

LOG_DIR = Path.home() / ".local" / "share" / "agent-interface" / "daemons"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daemons (
    repo        TEXT NOT NULL,
    name        TEXT NOT NULL,
    cmd         TEXT NOT NULL,
    pid         INTEGER,
    cwd         TEXT,
    log_path    TEXT,
    started_at  TEXT NOT NULL,
    stopped_at  TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    PRIMARY KEY (repo, name)
);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def _repo_key(cwd: str) -> str:
    """Git repo root for cwd, else cwd — so daemons are scoped per project."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cwd


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie (terminated, not yet reaped) still answers signal 0 but is gone.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        state = stat[stat.rfind(")") + 2:].split(" ", 1)[0]
        if state == "Z":
            return False
    except OSError:
        pass
    return True


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text).strip("-") or "x"


def _default_name(cmd: list[str]) -> str:
    return _slug(Path(cmd[0]).name)[:24] if cmd else "daemon"


def launch(cmd: list[str], *, name: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Launch a detached, logged background process and record it.

    Returns the ledger row (name, pid, log_path, ...). Raises ValueError if a
    daemon of the same name is already running in this project.
    """
    cwd = cwd or os.getcwd()
    name = name or _default_name(cmd)
    repo = _repo_key(cwd)
    conn = get_connection()
    _ensure(conn)

    existing = conn.execute(
        "SELECT pid, status FROM daemons WHERE repo=? AND name=?", (repo, name),
    ).fetchone()
    if existing and existing["status"] == "running" and _alive(existing["pid"]):
        raise ValueError(f"daemon '{name}' is already running (pid {existing['pid']})")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = str(LOG_DIR / f"{_slug(repo)[-40:]}-{_slug(name)}.log")

    logf = open(log_path, "ab")  # noqa: SIM115 — handed to the child process
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # setsid: detach from our session/pgroup
        )
    finally:
        logf.close()

    now = _now_utc()
    conn.execute(
        """INSERT INTO daemons (repo, name, cmd, pid, cwd, log_path, started_at, stopped_at, status)
           VALUES (?,?,?,?,?,?,?,NULL,'running')
           ON CONFLICT(repo, name) DO UPDATE SET
             cmd=excluded.cmd, pid=excluded.pid, cwd=excluded.cwd,
             log_path=excluded.log_path, started_at=excluded.started_at,
             stopped_at=NULL, status='running'""",
        (repo, name, " ".join(cmd), proc.pid, cwd, log_path, now),
    )
    conn.commit()
    return {
        "name": name, "repo": repo, "pid": proc.pid,
        "log_path": log_path, "cmd": " ".join(cmd), "status": "running",
    }


def list_daemons(cwd: Optional[str] = None, *, all_projects: bool = False) -> list[dict]:
    """List tracked daemons, refreshing liveness. Scoped to the project unless
    all_projects is set."""
    conn = get_connection()
    _ensure(conn)
    if all_projects:
        rows = conn.execute("SELECT * FROM daemons ORDER BY started_at DESC").fetchall()
    else:
        repo = _repo_key(cwd or os.getcwd())
        rows = conn.execute(
            "SELECT * FROM daemons WHERE repo=? ORDER BY started_at DESC", (repo,),
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        live = _alive(r["pid"]) if r["status"] == "running" else False
        # Reconcile: a 'running' daemon whose process is gone → mark exited.
        if r["status"] == "running" and not live:
            conn.execute(
                "UPDATE daemons SET status='exited', stopped_at=? WHERE repo=? AND name=?",
                (_now_utc(), r["repo"], r["name"]),
            )
            conn.commit()
        if live:
            status = "running"
        else:
            status = r["status"] if r["status"] != "running" else "exited"
        out.append({
            "name": r["name"], "repo": r["repo"], "pid": r["pid"], "cmd": r["cmd"],
            "log_path": r["log_path"], "started_at": r["started_at"], "status": status,
        })
    return out


def stop(name: str, *, cwd: Optional[str] = None, sig: int = signal.SIGTERM) -> bool:
    """Stop a daemon by name in the current project. Returns True if signalled."""
    repo = _repo_key(cwd or os.getcwd())
    conn = get_connection()
    _ensure(conn)
    row = conn.execute(
        "SELECT pid, status FROM daemons WHERE repo=? AND name=?", (repo, name),
    ).fetchone()
    if row is None:
        return False
    pid = row["pid"]
    signalled = False
    if pid and _alive(pid):
        try:
            os.kill(pid, sig)
            signalled = True
        except OSError:
            signalled = False
    conn.execute(
        "UPDATE daemons SET status='stopped', stopped_at=? WHERE repo=? AND name=?",
        (_now_utc(), repo, name),
    )
    conn.commit()
    return signalled
