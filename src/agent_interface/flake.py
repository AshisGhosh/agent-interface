"""Per-project flaky-test ledger — is this test really broken, or just flaky?

Agents doing sim / firmware / eval work hit the same trap over and over: a test
fails once (a drive motor stalls on ``block-rel-test``, a loop-back run flakes on
the jetson), they spend a session "investigating", and it turns out the thing
fails one run in five for reasons unrelated to their change. The opposite trap is
just as costly — treating a deterministic failure as "probably flaky, retry" and
shipping on top of it.

This records each test outcome (``pass`` / ``fail``) keyed by project and test
name, then surfaces a flakiness report: how often each test passes vs fails,
whether it is *flaky* (both pass and fail seen), *failing* (only ever fails), or
*passing*. An agent records results with ``agi flake <test> -s fail`` and the
next session reads the picture back with ``agi flakes`` before deciding whether a
red test is worth a deep dive.

Like the runbook and notebook it is project-scoped: the key is the git repo root
(falling back to the cwd), so results are shared across subdirectories of a repo
and never mix between projects. It works from any project directory.
"""

from __future__ import annotations

import time
from typing import Optional

# Project keying is shared with the runbook/notebook so `agi flake`, `agi run`
# and `agi note` all agree on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

STATUSES = ("pass", "fail")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    test        TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    duration_ms REAL,
    ran_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_test_results_project ON test_results(project);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def normalize_status(status: str) -> str:
    """Map common spellings of an outcome onto ``pass`` / ``fail``.

    Accepts pytest-ish synonyms so an agent can pipe through whatever word it has
    (``passed``, ``ok``, ``green`` → pass; ``failed``, ``error``, ``red`` → fail).
    Raises :class:`ValueError` on anything unrecognised.
    """
    s = status.strip().lower()
    if s in {"pass", "passed", "ok", "green", "p", "0"}:
        return "pass"
    if s in {"fail", "failed", "failure", "error", "red", "f", "1"}:
        return "fail"
    raise ValueError(f"unknown status {status!r} (expected pass/fail)")


def classify(passes: int, fails: int) -> str:
    """Label a test from its pass/fail tallies.

    ``flaky`` (both outcomes seen), ``failing`` (only fails), ``passing`` (only
    passes), or ``unknown`` (no recorded runs).
    """
    if passes and fails:
        return "flaky"
    if fails:
        return "failing"
    if passes:
        return "passing"
    return "unknown"


def record_result(
    conn,
    *,
    project: str,
    test: str,
    status: str,
    note: Optional[str] = None,
    duration_ms: Optional[float] = None,
    ran_at: Optional[float] = None,
) -> int:
    """Persist one test outcome for *project* and return its row id.

    *status* is normalised via :func:`normalize_status`, so callers may pass
    ``"failed"``/``"passed"`` etc.
    """
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO test_results (project, test, status, note, duration_ms, ran_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            project,
            test,
            normalize_status(status),
            note,
            duration_ms,
            time.time() if ran_at is None else ran_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def flaky_stats(
    conn,
    project: str,
    *,
    name: Optional[str] = None,
    flaky_only: bool = False,
) -> list[dict]:
    """Per-test flakiness summary for *project*, most-interesting-first.

    Each entry is a dict with: ``test``, ``total``, ``passes``, ``fails``,
    ``fail_rate`` (0..1), ``kind`` (see :func:`classify`), ``last_status``,
    ``last_seen`` (epoch seconds) and ``last_note``.

    *name* is a case-insensitive substring filter on the test name. When
    *flaky_only* is set, deterministic (pure-pass / pure-fail) tests are dropped.
    Ordering: flaky first, then by fail-rate, then most-recently-seen.
    """
    _ensure(conn)
    where = ["project=?"]
    params: list = [project]
    if name is not None:
        where.append("LOWER(test) LIKE ?")
        params.append(f"%{name.lower()}%")
    # Ascending by id so the final row seen per test is the latest outcome.
    rows = conn.execute(
        f"SELECT test, status, note, ran_at FROM test_results "
        f"WHERE {' AND '.join(where)} ORDER BY id ASC",
        params,
    ).fetchall()

    agg: dict[str, dict] = {}
    for r in rows:
        t = r["test"]
        e = agg.get(t)
        if e is None:
            e = agg[t] = {
                "test": t,
                "total": 0,
                "passes": 0,
                "fails": 0,
                "last_status": None,
                "last_seen": None,
                "last_note": None,
            }
        e["total"] += 1
        if r["status"] == "pass":
            e["passes"] += 1
        else:
            e["fails"] += 1
        e["last_status"] = r["status"]
        e["last_seen"] = r["ran_at"]
        e["last_note"] = r["note"]

    out = []
    for e in agg.values():
        e["kind"] = classify(e["passes"], e["fails"])
        e["fail_rate"] = e["fails"] / e["total"] if e["total"] else 0.0
        if flaky_only and e["kind"] != "flaky":
            continue
        out.append(e)

    out.sort(
        key=lambda e: (
            e["kind"] != "flaky",  # flaky tests first
            -e["fail_rate"],
            -(e["last_seen"] or 0.0),
        )
    )
    return out


def history(conn, project: str, test: str, *, limit: int = 20) -> list:
    """Most-recent-first raw outcomes for a single *test* in *project*."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT * FROM test_results WHERE project=? AND test=? "
        "ORDER BY id DESC LIMIT ?",
        (project, test, limit),
    ).fetchall()
    return list(rows)


def clear_results(conn, project: str, *, test: Optional[str] = None) -> int:
    """Delete recorded outcomes for *project* (optionally a single *test*).

    Returns the number of rows removed. Scoping every delete to the project means
    a test name from another project can never wipe the wrong ledger.
    """
    _ensure(conn)
    if test is not None:
        cur = conn.execute(
            "DELETE FROM test_results WHERE project=? AND test=?", (project, test)
        )
    else:
        cur = conn.execute("DELETE FROM test_results WHERE project=?", (project,))
    conn.commit()
    return cur.rowcount
