"""Per-project experiment findings ledger — log and compare result metrics.

Research/ML agents iterate over *variants* of an experiment ("v3:
distance-prediction aux task replacing minimap recon") and the thing that
actually matters — "which variant won, on which metric" — ends up scattered
across scrollback, figures, and one-off print statements. The next session
(often the same agent after a context reset) is left "looking at all the
outputs and figures and logged findings" trying to reconstruct what beat what.

This is a tiny, structured ledger for exactly that. An agent records a result
for a labeled variant with ``agi finding <label> --metric <m> --value <v>``,
and a later session reads them back with ``agi findings`` or ranks the variants
head-to-head with ``agi findings --compare --metric <m>``.

It is deliberately distinct from the freeform notebook
(:mod:`agent_interface.notes`, ``agi note``) and the command runbook
(:mod:`agent_interface.runlog`, ``agi run``): notes capture prose, runs capture
commands, findings capture *numeric results you can rank*. Like both it is
project-scoped — keyed by the git repo root (falling back to the cwd) via the
shared :func:`project_key` — so findings from different projects never mix and
it works from any project directory.
"""

from __future__ import annotations

import time
from typing import Optional

# Share project keying with the runbook/notebook so `agi finding`, `agi note`
# and `agi run` all agree on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    label       TEXT NOT NULL,
    metric      TEXT,
    value       REAL,
    note        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiment_findings_project
    ON experiment_findings(project);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def record_finding(
    conn,
    *,
    project: str,
    label: str,
    metric: Optional[str] = None,
    value: Optional[float] = None,
    note: Optional[str] = None,
    created_at: Optional[float] = None,
) -> int:
    """Persist one finding for *project* and return its row id.

    *label* names the variant/experiment (e.g. ``"v3-distance-pred"``). The
    optional *metric*/*value* pair is the result worth comparing later (e.g.
    ``"val_loss"``, ``0.23``); *note* is freeform context.
    """
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO experiment_findings "
        "(project, label, metric, value, note, created_at) VALUES (?,?,?,?,?,?)",
        (
            project,
            label,
            metric,
            value,
            note,
            time.time() if created_at is None else created_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_findings(
    conn,
    project: str,
    *,
    metric: Optional[str] = None,
    label: Optional[str] = None,
    limit: int = 50,
) -> list:
    """Most-recent-first findings for *project*, optionally filtered.

    *metric* and *label* both match exactly and may be combined.
    """
    _ensure(conn)
    where = ["project=?"]
    params: list = [project]
    if metric is not None:
        where.append("metric=?")
        params.append(metric)
    if label is not None:
        where.append("label=?")
        params.append(label)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM experiment_findings WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return list(rows)


def compare_findings(
    conn,
    project: str,
    metric: str,
    *,
    higher_is_better: bool = True,
) -> list[dict]:
    """Rank variants for *project* by their best recorded *metric* value.

    Returns one entry per distinct ``label`` that has a numeric value for
    *metric*, sorted best-first. Each entry is a dict with ``label``, ``best``
    (the winning value), ``runs`` (how many values were recorded for that
    label/metric), and ``last_at`` (timestamp of the most recent record). Ties
    keep a stable, label-alphabetical order so the output is deterministic.
    """
    _ensure(conn)
    rows = conn.execute(
        "SELECT label, value, created_at FROM experiment_findings "
        "WHERE project=? AND metric=? AND value IS NOT NULL",
        (project, metric),
    ).fetchall()

    agg: dict[str, dict] = {}
    for r in rows:
        label = r["label"]
        value = r["value"]
        cur = agg.get(label)
        if cur is None:
            agg[label] = {
                "label": label,
                "best": value,
                "runs": 1,
                "last_at": r["created_at"],
            }
            continue
        cur["runs"] += 1
        cur["last_at"] = max(cur["last_at"], r["created_at"])
        if (higher_is_better and value > cur["best"]) or (
            not higher_is_better and value < cur["best"]
        ):
            cur["best"] = value

    # Sort best-first; break ties by label for deterministic output.
    return sorted(
        agg.values(),
        key=lambda e: (-e["best"] if higher_is_better else e["best"], e["label"]),
    )


def remove_finding(conn, project: str, finding_id: int) -> bool:
    """Delete one finding by id, scoped to *project*. Returns True if a row went.

    Scoping the delete to the project means an id from another project's ledger
    can never remove the wrong finding.
    """
    _ensure(conn)
    cur = conn.execute(
        "DELETE FROM experiment_findings WHERE id=? AND project=?",
        (finding_id, project),
    )
    conn.commit()
    return cur.rowcount > 0
