"""First-class project dashboards — declare once, keep up, open easily.

Long-running projects each tend to have a primary surface: a web dashboard, a
lab notebook, a metrics UI. `agi up` can launch one, but you still have to
remember the command. A *dashboard* is a named, declared, supervised daemon
with an optional URL:

  - declared once per project (`agi dash add`), so any agent/session can bring
    it up without knowing the command,
  - supervised: the heartbeat relaunches it if it crashes or after a reboot, and
  - addressable: it carries a URL so `agi dash open` just works.

Built on top of :mod:`agent_interface.daemon` — a dashboard's process IS a
daemon sharing its name, so all the detach/log/liveness machinery is reused.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from agent_interface import daemon
from agent_interface.db import get_connection
from agent_interface.models import _now_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboards (
    repo        TEXT NOT NULL,
    name        TEXT NOT NULL,
    argv        TEXT NOT NULL,
    url         TEXT,
    cwd         TEXT,
    supervised  INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (repo, name)
);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def declare(
    name: str,
    cmd: list[str],
    *,
    url: Optional[str] = None,
    cwd: Optional[str] = None,
    supervised: bool = True,
) -> dict:
    """Register (or update) a project's dashboard."""
    cwd = cwd or os.getcwd()
    repo = daemon._repo_key(cwd)
    conn = get_connection()
    _ensure(conn)
    conn.execute(
        """INSERT INTO dashboards (repo, name, argv, url, cwd, supervised, created_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(repo, name) DO UPDATE SET
             argv=excluded.argv, url=excluded.url, cwd=excluded.cwd,
             supervised=excluded.supervised""",
        (repo, name, json.dumps(cmd), url, cwd, int(supervised), _now_utc()),
    )
    conn.commit()
    return {"repo": repo, "name": name, "cmd": " ".join(cmd), "url": url, "supervised": supervised}


def remove(name: str, *, cwd: Optional[str] = None) -> bool:
    repo = daemon._repo_key(cwd or os.getcwd())
    conn = get_connection()
    _ensure(conn)
    cur = conn.execute("DELETE FROM dashboards WHERE repo=? AND name=?", (repo, name))
    conn.commit()
    return cur.rowcount > 0


def _running(conn, repo: str, name: str) -> tuple[bool, Optional[int]]:
    row = conn.execute(
        "SELECT pid, status FROM daemons WHERE repo=? AND name=?", (repo, name),
    ).fetchone()
    if row is None or row["status"] != "running":
        return False, None
    return daemon._alive(row["pid"]), row["pid"]


def _rows(conn, repo: Optional[str], all_projects: bool):
    if all_projects:
        return conn.execute("SELECT * FROM dashboards ORDER BY repo, name").fetchall()
    return conn.execute(
        "SELECT * FROM dashboards WHERE repo=? ORDER BY name", (repo,),
    ).fetchall()


def list_dashboards(cwd: Optional[str] = None, *, all_projects: bool = False) -> list[dict]:
    """Declared dashboards with live status (from the daemon ledger)."""
    conn = get_connection()
    _ensure(conn)
    daemon._ensure(conn)
    repo = daemon._repo_key(cwd or os.getcwd())
    out: list[dict] = []
    for r in _rows(conn, repo, all_projects):
        live, pid = _running(conn, r["repo"], r["name"])
        out.append({
            "repo": r["repo"], "name": r["name"], "cmd": " ".join(json.loads(r["argv"])),
            "url": r["url"], "supervised": bool(r["supervised"]),
            "status": "up" if live else "down", "pid": pid if live else None,
        })
    return out


def up(name: Optional[str] = None, *, cwd: Optional[str] = None) -> list[dict]:
    """Bring up the named dashboard (or all in this project). Idempotent."""
    conn = get_connection()
    _ensure(conn)
    daemon._ensure(conn)
    repo = daemon._repo_key(cwd or os.getcwd())
    rows = conn.execute(
        "SELECT * FROM dashboards WHERE repo=?" + ("" if name is None else " AND name=?"),
        (repo,) if name is None else (repo, name),
    ).fetchall()

    results: list[dict] = []
    for r in rows:
        live, _ = _running(conn, r["repo"], r["name"])
        if live:
            results.append({"name": r["name"], "status": "already up", "url": r["url"]})
            continue
        info = daemon.launch(json.loads(r["argv"]), name=r["name"], cwd=r["cwd"])
        results.append({
            "name": r["name"], "status": "started", "url": r["url"],
            "pid": info["pid"], "log_path": info["log_path"],
        })
    return results


def ensure_up() -> dict:
    """Heartbeat hook: relaunch any supervised dashboard that isn't running.

    This is what makes a dashboard 'never go down' — it's restarted on crash
    and after reboot (the heartbeat runs under the systemd timer).
    """
    conn = get_connection()
    _ensure(conn)
    daemon._ensure(conn)
    restarted: list[str] = []
    for r in conn.execute("SELECT * FROM dashboards WHERE supervised=1").fetchall():
        live, _ = _running(conn, r["repo"], r["name"])
        if live:
            continue
        try:
            daemon.launch(json.loads(r["argv"]), name=r["name"], cwd=r["cwd"])
            restarted.append(f"{r['name']}@{r['repo']}")
        except Exception:  # noqa: BLE001 — best-effort; never break the heartbeat
            pass
    return {"restarted": restarted}


def get(name: str, *, cwd: Optional[str] = None) -> Optional[dict]:
    repo = daemon._repo_key(cwd or os.getcwd())
    conn = get_connection()
    _ensure(conn)
    r = conn.execute(
        "SELECT * FROM dashboards WHERE repo=? AND name=?", (repo, name),
    ).fetchone()
    if r is None:
        return None
    return {"repo": r["repo"], "name": r["name"], "url": r["url"],
            "cmd": " ".join(json.loads(r["argv"]))}
