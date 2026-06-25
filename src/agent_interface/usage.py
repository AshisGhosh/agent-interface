"""Feature-usage ledger — the 'or else it is a failure' half of the loop.

Auto-shipping a feature is only half the job; the goal is that the feature
gets *used*. This records invocations of autonomously-shipped features and lets
the optimizer judge each one: a feature with zero recorded uses past its grace
window is a failure, not a success.

Shipped features call :func:`record_usage` (directly, or via `agi usage record
<feature-id>`) at their entry point. The optimizer reads the counts back.
"""

from __future__ import annotations

import time
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id  TEXT NOT NULL,
    repo        TEXT,
    source      TEXT,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feature_usage_fid ON feature_usage(feature_id);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def record_usage(
    feature_id: str,
    *,
    repo: Optional[str] = None,
    source: Optional[str] = None,
    conn=None,
    now: Optional[float] = None,
) -> None:
    """Record one use of a shipped feature. Best-effort; never raises."""
    try:
        own = conn is None
        if own:
            from agent_interface.db import get_connection
            conn = get_connection()
        _ensure(conn)
        conn.execute(
            "INSERT INTO feature_usage (feature_id, repo, source, ts) VALUES (?,?,?,?)",
            (feature_id, repo, source, time.time() if now is None else now),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — usage tracking must never break a feature
        pass


def usage_count(conn, feature_id: str, *, since: Optional[float] = None) -> int:
    """How many times a feature has been used (optionally since a timestamp)."""
    _ensure(conn)
    if since is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM feature_usage WHERE feature_id=?", (feature_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM feature_usage WHERE feature_id=? AND ts>=?",
            (feature_id, since),
        ).fetchone()
    return int(row[0])


def usage_summary(conn) -> dict[str, int]:
    """Total uses per feature_id."""
    _ensure(conn)
    rows = conn.execute(
        "SELECT feature_id, COUNT(*) FROM feature_usage GROUP BY feature_id",
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}
