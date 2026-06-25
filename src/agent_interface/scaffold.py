"""Reusable code-scaffold library — save a component template once, stamp it out
anywhere.

Coding agents keep rebuilding the *same shape* of thing across projects and
across sessions: a UI component skeleton, an input/touch-controls handler, a
test harness, a CLI subcommand. The good version of that skeleton gets written
once and then re-derived from scratch the next time, because the knowledge died
with the session. (The motivating example: a Spellblade "spell UI component"
plus a "touch-controls" handler — exactly the kind of boilerplate you want to
reuse, not retype.)

This is a tiny library for exactly that. An agent saves a named *scaffold* — a
code template with ``{{placeholder}}`` holes — with ``agi scaffold save``, and a
later session in any project stamps a filled-in copy to a file with
``agi scaffold new <name> <dest> --var key=value``.

Scaffolds are **global by default** so they travel across every project; pass
``--project`` to save one scoped to the current repo (git root, else cwd) when
the template only makes sense there. On lookup a project-scoped scaffold shadows
a global one of the same name, so a repo can specialize a shared template.

It is deliberately distinct from the other ledgers: ``agi note`` captures prose,
``agi run`` captures commands, ``agi finding`` captures numeric results — this
captures *renderable code templates*.
"""

from __future__ import annotations

import re
import time
from typing import Optional

# Share project keying with the runbook/notebook/findings so every project-scoped
# agi tool agrees on what "this project" means.
from agent_interface.runlog import project_key  # noqa: F401 (re-exported)

GLOBAL_SCOPE = "global"

# A placeholder is ``{{name}}`` with optional surrounding whitespace; the name is
# a conventional identifier so it round-trips cleanly through --var key=value.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scaffolds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    name        TEXT NOT NULL,
    body        TEXT NOT NULL,
    description TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(scope, name)
);
CREATE INDEX IF NOT EXISTS idx_scaffolds_scope ON scaffolds(scope);
"""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def save_scaffold(
    conn,
    *,
    name: str,
    body: str,
    scope: str = GLOBAL_SCOPE,
    description: Optional[str] = None,
    now: Optional[float] = None,
) -> tuple[int, bool]:
    """Create or update a scaffold; return ``(id, created)``.

    Saving a name that already exists in *scope* overwrites its body and
    description (preserving the original ``created_at``) so iterating on a
    template is a re-save, not a duplicate. ``created`` is True only on first
    insert.
    """
    _ensure(conn)
    ts = time.time() if now is None else now
    existing = conn.execute(
        "SELECT id FROM scaffolds WHERE scope=? AND name=?", (scope, name),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE scaffolds SET body=?, description=?, updated_at=? "
            "WHERE scope=? AND name=?",
            (body, description, ts, scope, name),
        )
        conn.commit()
        return int(existing[0]), False
    cur = conn.execute(
        "INSERT INTO scaffolds (scope, name, body, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (scope, name, body, description, ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid), True


def get_scaffold(conn, name: str, *, project: Optional[str] = None):
    """Resolve a scaffold by *name*, returning its row or None.

    A project-scoped scaffold shadows a global one of the same name, so when
    *project* is given that scope is consulted first.
    """
    _ensure(conn)
    if project is not None:
        row = conn.execute(
            "SELECT * FROM scaffolds WHERE scope=? AND name=?", (project, name),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        "SELECT * FROM scaffolds WHERE scope=? AND name=?", (GLOBAL_SCOPE, name),
    ).fetchone()


def list_scaffolds(conn, *, project: Optional[str] = None) -> list:
    """All scaffolds visible from *project* — globals plus that project's own.

    Sorted by name (project scope before global on a tie) so the listing is
    stable and a project's specialization sorts next to the global it shadows.
    """
    _ensure(conn)
    if project is not None and project != GLOBAL_SCOPE:
        rows = conn.execute(
            "SELECT * FROM scaffolds WHERE scope IN (?, ?)",
            (GLOBAL_SCOPE, project),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scaffolds WHERE scope=?", (GLOBAL_SCOPE,),
        ).fetchall()
    # Project scope sorts before global so a specialization leads its global twin.
    return sorted(
        rows, key=lambda r: (r["name"], r["scope"] == GLOBAL_SCOPE),
    )


def remove_scaffold(conn, name: str, *, scope: str = GLOBAL_SCOPE) -> bool:
    """Delete the scaffold named *name* in *scope*. Returns True if one went."""
    _ensure(conn)
    cur = conn.execute(
        "DELETE FROM scaffolds WHERE scope=? AND name=?", (scope, name),
    )
    conn.commit()
    return cur.rowcount > 0


def placeholders(body: str) -> list[str]:
    """The distinct ``{{placeholder}}`` names in *body*, in first-seen order."""
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(body):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def render(body: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Fill ``{{placeholder}}`` holes in *body* from *variables*.

    Returns ``(rendered, missing)`` where *missing* lists, in first-seen order,
    the placeholders that had no value supplied. Unfilled holes are left verbatim
    in the output so a partially-rendered file is still obviously a template.
    """
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return variables[name]
        if name not in missing:
            missing.append(name)
        return m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body), missing


def parse_var(item: str) -> tuple[str, str]:
    """Parse a ``key=value`` --var item into a ``(key, value)`` pair.

    The value may itself contain ``=`` (only the first splits). Raises
    ``ValueError`` if there is no ``=`` or the key is empty.
    """
    key, sep, value = item.partition("=")
    key = key.strip()
    if not sep or not key:
        raise ValueError(f"expected key=value, got {item!r}")
    return key, value
