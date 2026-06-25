"""Per-project iteration assessment rubric — score qualitative work as it iterates.

Some of the most common agent work is *qualitative iteration*: overhaul the art,
re-tune the procedural generator, redo the lighting, look at the result, decide
whether it's better, tweak, repeat. The "look at the result and decide" step —
the eval/assessment — is exactly the knowledge that dies with the session. The
next iteration (often the same agent after a context reset) can't remember
whether the last pass actually improved the silhouette readability or made the
palette worse, so it re-judges from scratch or, worse, regresses something it
already fixed.

This is a tiny, structured rubric for exactly that loop. An agent assesses the
*current iteration* of a *subject* (e.g. ``art-overhaul``) by scoring a handful
of named criteria — ``agi assess art-overhaul -c lighting=7 -c palette=5`` — plus
an optional verdict and note. Each assessment is one iteration; the iteration
number is assigned automatically. A later session reads the history back with
``agi assessments art-overhaul`` and, crucially, sees the **per-criterion trend**
across iterations (``--trend``) — which criteria are improving, which regressed,
and by how much.

It is deliberately distinct from the experiment findings ledger
(:mod:`agent_interface.findings`, ``agi finding``): findings rank *variants*
head-to-head on a single numeric *metric* (which variant won). Assessments track
*iterations* of one subject across *multiple* rubric criteria over time (is this
subject getting better, criterion by criterion). Like the rest of the toolkit it
is project-scoped via the shared :func:`project_key` — keyed by the git repo root
(falling back to the cwd) so assessments from different projects never mix — and
it works from any project directory.
"""

from __future__ import annotations

import time
from typing import Iterable, Optional, Union

# Share project keying with the runbook/notebook/findings so every `agi`
# subcommand agrees on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assessments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    iteration   INTEGER NOT NULL,
    verdict     TEXT,
    note        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessments_project
    ON assessments(project, subject);

CREATE TABLE IF NOT EXISTS assessment_scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    criterion     TEXT NOT NULL,
    score         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessment_scores_aid
    ON assessment_scores(assessment_id);
"""

# Criteria/scores may arrive as a dict or as an ordered list of (name, score).
ScoreInput = Union[dict, Iterable[tuple]]


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def parse_criterion(raw: str) -> tuple[str, float]:
    """Parse a ``name=score`` CLI token into ``(name, float_score)``.

    Raises :class:`ValueError` with an actionable message if the token is not of
    the form ``name=number`` (e.g. missing ``=`` or a non-numeric score).
    """
    if "=" not in raw:
        raise ValueError(
            f"criterion {raw!r} must be name=score, e.g. lighting=7"
        )
    name, _, value = raw.partition("=")
    name = name.strip()
    value = value.strip()
    if not name:
        raise ValueError(f"criterion {raw!r} has an empty name")
    try:
        score = float(value)
    except ValueError:
        raise ValueError(
            f"criterion {raw!r}: score {value!r} is not a number"
        ) from None
    return name, score


def _normalize_scores(scores: ScoreInput) -> list[tuple[str, float]]:
    """Coerce a dict or iterable of pairs into an ordered, de-duplicated list.

    Later values for the same criterion win (so a repeated ``-c name=...`` on the
    CLI overrides the earlier one) while preserving first-seen order.
    """
    items = scores.items() if isinstance(scores, dict) else scores
    ordered: list[str] = []
    seen: dict[str, float] = {}
    for name, value in items:
        name = str(name).strip()
        if not name:
            raise ValueError("criterion name must not be empty")
        if name not in seen:
            ordered.append(name)
        seen[name] = float(value)
    return [(name, seen[name]) for name in ordered]


def next_iteration(conn, project: str, subject: str) -> int:
    """The iteration number the next assessment of *subject* would get (1-based)."""
    _ensure(conn)
    row = conn.execute(
        "SELECT MAX(iteration) FROM assessments WHERE project=? AND subject=?",
        (project, subject),
    ).fetchone()
    return (row[0] or 0) + 1


def record_assessment(
    conn,
    *,
    project: str,
    subject: str,
    scores: Optional[ScoreInput] = None,
    verdict: Optional[str] = None,
    note: Optional[str] = None,
    iteration: Optional[int] = None,
    created_at: Optional[float] = None,
) -> dict:
    """Persist one iteration's assessment of *subject* and return its summary.

    *scores* maps rubric criteria to numeric scores (a dict, or an ordered list
    of ``(name, score)`` pairs). *verdict* is an optional short call ("pass",
    "ship", "needs-work") and *note* is freeform context. *iteration* is assigned
    automatically (previous max for this subject, plus one) unless given.

    Returns ``{"id", "iteration", "scores"}`` where ``scores`` is the normalized
    ordered list actually stored.
    """
    _ensure(conn)
    pairs = _normalize_scores(scores) if scores else []
    if iteration is None:
        iteration = next_iteration(conn, project, subject)
    ts = time.time() if created_at is None else created_at
    cur = conn.execute(
        "INSERT INTO assessments (project, subject, iteration, verdict, note, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (project, subject, iteration, verdict, note, ts),
    )
    aid = int(cur.lastrowid)
    for name, score in pairs:
        conn.execute(
            "INSERT INTO assessment_scores (assessment_id, criterion, score) "
            "VALUES (?,?,?)",
            (aid, name, score),
        )
    conn.commit()
    return {"id": aid, "iteration": iteration, "scores": pairs}


def _attach_scores(conn, rows: list) -> list[dict]:
    """Turn assessment rows into dicts with their criteria scores attached."""
    out: list[dict] = []
    for r in rows:
        score_rows = conn.execute(
            "SELECT criterion, score FROM assessment_scores "
            "WHERE assessment_id=? ORDER BY id",
            (r["id"],),
        ).fetchall()
        out.append(
            {
                "id": r["id"],
                "subject": r["subject"],
                "iteration": r["iteration"],
                "verdict": r["verdict"],
                "note": r["note"],
                "created_at": r["created_at"],
                "scores": [(s["criterion"], s["score"]) for s in score_rows],
            }
        )
    return out


def list_assessments(
    conn,
    project: str,
    *,
    subject: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Most-recent-first assessments for *project*, optionally one *subject*.

    Each entry is a dict with ``id``, ``subject``, ``iteration``, ``verdict``,
    ``note``, ``created_at`` and an ordered ``scores`` list of ``(criterion,
    score)`` pairs.
    """
    _ensure(conn)
    where = ["project=?"]
    params: list = [project]
    if subject is not None:
        where.append("subject=?")
        params.append(subject)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM assessments WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return _attach_scores(conn, list(rows))


def latest_assessment(conn, project: str, subject: str) -> Optional[dict]:
    """The most recent assessment of *subject*, or ``None``."""
    rows = list_assessments(conn, project, subject=subject, limit=1)
    return rows[0] if rows else None


def assessment_trend(conn, project: str, subject: str) -> list[dict]:
    """Per-criterion progression of *subject* across its iterations.

    Returns one entry per criterion ever scored for *subject*, sorted
    alphabetically for deterministic output. Each entry is a dict with:

    * ``criterion`` — the rubric criterion name.
    * ``points`` — ``[(iteration, score), ...]`` in iteration order (one per
      iteration that scored this criterion).
    * ``first`` / ``last`` — the earliest and latest scores.
    * ``delta`` — ``last - first`` (0.0 when only one point exists).
    * ``direction`` — ``"up"``, ``"down"`` or ``"flat"`` from the sign of delta.
    """
    _ensure(conn)
    rows = conn.execute(
        "SELECT a.iteration AS iteration, s.criterion AS criterion, s.score AS score "
        "FROM assessment_scores s JOIN assessments a ON s.assessment_id = a.id "
        "WHERE a.project=? AND a.subject=? "
        "ORDER BY s.criterion, a.iteration",
        (project, subject),
    ).fetchall()

    by_criterion: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        by_criterion.setdefault(r["criterion"], []).append(
            (r["iteration"], r["score"])
        )

    out: list[dict] = []
    for criterion in sorted(by_criterion):
        points = by_criterion[criterion]
        first = points[0][1]
        last = points[-1][1]
        delta = last - first
        direction = "flat" if delta == 0 else ("up" if delta > 0 else "down")
        out.append(
            {
                "criterion": criterion,
                "points": points,
                "first": first,
                "last": last,
                "delta": delta,
                "direction": direction,
            }
        )
    return out


def remove_assessment(conn, project: str, assessment_id: int) -> bool:
    """Delete one assessment (and its scores) by id, scoped to *project*.

    Scoping the delete to the project means an id from another project's history
    can never remove the wrong assessment. Returns True if a row went.
    """
    _ensure(conn)
    cur = conn.execute(
        "DELETE FROM assessments WHERE id=? AND project=?",
        (assessment_id, project),
    )
    if cur.rowcount:
        conn.execute(
            "DELETE FROM assessment_scores WHERE assessment_id=?",
            (assessment_id,),
        )
    conn.commit()
    return cur.rowcount > 0
