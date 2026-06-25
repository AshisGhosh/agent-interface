"""Per-project cluster/remote job ledger — track long-running jobs across sessions.

Agents working on ML projects routinely submit a job to a remote cluster (an
H100 SLURM allocation, a batch run, a sweep), get back a *job id*, and start an
AIM run that streams metrics to a remote URL. None of that survives the session:
the next agent — or the same one after a context reset — has no idea which jobs
are in flight, what their cluster ids are, or where to point ``aim up`` to watch
them. They re-submit, or lose the run.

This is a tiny project-scoped ledger for exactly that handoff. An agent records a
job with ``agi job "<title>" --id <cluster-id> --aim <run-or-url>`` when it
submits, bumps the status with ``agi job --update <n> --status running``, and the
next session reads the in-flight jobs back with ``agi jobs`` — cluster id and AIM
streaming URL in hand.

Like the runbook (:mod:`agent_interface.runlog`) and notebook
(:mod:`agent_interface.notes`) it is project-scoped: the key is the git repo root
(falling back to the cwd), so jobs are shared across subdirectories of a repo and
never mix between projects. It works from any project directory.
"""

from __future__ import annotations

import time
from typing import Optional

# Share project keying with the runbook/notebook so every `agi` project-scoped
# feature agrees on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

# Lifecycle of a tracked job. Open statuses are still in flight (an agent should
# keep watching them); terminal statuses are settled.
STATUSES = ("submitted", "running", "done", "failed", "cancelled")
OPEN_STATUSES = ("submitted", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    title       TEXT NOT NULL,
    job_id      TEXT,
    aim         TEXT,
    status      TEXT NOT NULL DEFAULT 'submitted',
    note        TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cluster_jobs_project ON cluster_jobs(project);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def add_job(
    conn,
    *,
    project: str,
    title: str,
    job_id: Optional[str] = None,
    aim: Optional[str] = None,
    status: str = "submitted",
    note: Optional[str] = None,
    now: Optional[float] = None,
) -> int:
    """Record one submitted job for *project* and return its row id."""
    _ensure(conn)
    ts = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO cluster_jobs "
        "(project, title, job_id, aim, status, note, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (project, title, job_id, aim, status, note, ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_job(
    conn,
    project: str,
    row_id: int,
    *,
    job_id: Optional[str] = None,
    aim: Optional[str] = None,
    status: Optional[str] = None,
    note: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Patch the given fields of one tracked job, scoped to *project*.

    Only the fields passed (non-None) are changed; the rest are left as-is.
    Returns True if a row was updated. Scoping to the project means an id from
    another project's ledger can never patch the wrong job.
    """
    _ensure(conn)
    sets: list[str] = []
    params: list = []
    for col, val in (("job_id", job_id), ("aim", aim), ("status", status), ("note", note)):
        if val is not None:
            sets.append(f"{col}=?")
            params.append(val)
    if not sets:
        return False  # nothing to change
    sets.append("updated_at=?")
    params.append(time.time() if now is None else now)
    params.extend([row_id, project])
    cur = conn.execute(
        f"UPDATE cluster_jobs SET {', '.join(sets)} WHERE id=? AND project=?",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def list_jobs(
    conn,
    project: str,
    *,
    status: Optional[str] = None,
    open_only: bool = False,
    limit: int = 50,
) -> list:
    """Most-recent-first jobs for *project*, optionally filtered.

    *status* matches one status exactly; *open_only* keeps just the in-flight
    jobs (submitted/running). Ordered by last update so freshly-touched jobs
    surface first.
    """
    _ensure(conn)
    where = ["project=?"]
    params: list = [project]
    if status is not None:
        where.append("status=?")
        params.append(status)
    if open_only:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        where.append(f"status IN ({placeholders})")
        params.extend(OPEN_STATUSES)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM cluster_jobs WHERE {' AND '.join(where)} "
        "ORDER BY updated_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return list(rows)


def get_job(conn, project: str, row_id: int):
    """One tracked job by id, scoped to *project*, or None."""
    _ensure(conn)
    row = conn.execute(
        "SELECT * FROM cluster_jobs WHERE id=? AND project=?",
        (row_id, project),
    ).fetchone()
    return row


def remove_job(conn, project: str, row_id: int) -> bool:
    """Delete one tracked job by id, scoped to *project*. True if a row went."""
    _ensure(conn)
    cur = conn.execute(
        "DELETE FROM cluster_jobs WHERE id=? AND project=?",
        (row_id, project),
    )
    conn.commit()
    return cur.rowcount > 0
