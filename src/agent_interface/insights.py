"""Workflow discovery — mine the session registry for recurring agent work.

The registry accumulates one row per coding-agent session, each with a label
(the task the agent worked on) and a cwd/repo. Over time this is a record of
*what kinds of work happen on this machine*. This module clusters that history
by project and surfaces the recurring workflows — the raw material the
autonomous optimizer turns into reusable automations.

Everything here is pure (takes a connection, returns dataclasses) so it is
cheap to test and safe to call from the heartbeat.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Words that carry no signal about *what kind* of work a session did.
_STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "be", "this", "that", "it", "with", "as", "at", "by", "from", "into", "we",
    "i", "you", "my", "me", "can", "could", "should", "would", "do", "does",
    "did", "how", "what", "why", "when", "where", "all", "some", "out", "up",
    "if", "so", "but", "not", "no", "yes", "via", "use", "using", "get", "got",
    "make", "made", "need", "want", "like", "here", "there", "now", "then",
    "its", "was", "were", "has", "have", "had", "will", "just", "more", "new",
    "agi", "task", "claude", "session", "please", "lets", "let",
    # Dispatch boilerplate — every autonomously-dispatched agent's label starts
    # "...autonomous agent working on...", which would otherwise dominate.
    "working", "agent", "autonomous", "implement", "implementing", "end",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
# Task ids (t-8a19ca73) and bare hex blobs carry no workflow signal.
_TASKID_RE = re.compile(r"^t-[0-9a-f]{6,}$|^[0-9a-f]{8,}$")
# Paths that aren't real projects (tool installs, site-packages, venvs).
_NON_PROJECT = ("/.local/share/uv/", "/site-packages", "/.venv/", "/lib/python")


@dataclass
class WorkflowOpportunity:
    """A project with enough recurring agent activity to be worth automating."""

    repo: str
    session_count: int
    keywords: list[tuple[str, int]] = field(default_factory=list)
    sample_labels: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Rank opportunities: volume, amplified by how focused the work is.

        A repo with many sessions all hitting the same few keywords is a
        stronger automation candidate than one with scattered one-offs.
        """
        if not self.keywords:
            return float(self.session_count)
        top = sum(c for _, c in self.keywords[:3])
        concentration = top / max(self.session_count, 1)
        return self.session_count * (1.0 + concentration)


def _tokenize(text: str) -> list[str]:
    out = []
    for raw in _TOKEN_RE.findall(text or ""):
        t = raw.lower()
        if t in _STOPWORDS or _TASKID_RE.match(t):
            continue
        out.append(t)
    return out


def _normalize_repo(path: str) -> str:
    """Collapse a git worktree path back to its parent repo.

    Dispatched agents run in `<repo>/.worktrees/<task-id>`; without this each
    worktree looks like a separate project and the real repo's history is
    fragmented across dozens of one-off keys.
    """
    marker = "/.worktrees/"
    if marker in path:
        return path[: path.index(marker)]
    return path


def _repo_key(row) -> str | None:
    """The grouping key for a session: repo root if known, else cwd.

    Returns None for paths that aren't real projects (tool installs, venvs),
    which otherwise float to the top on dispatch noise.
    """
    raw = row["repo_root"] or row["cwd"]
    if not raw:
        return None
    if any(seg in raw for seg in _NON_PROJECT):
        return None
    return _normalize_repo(raw)


def analyze_sessions(
    conn,
    *,
    min_sessions: int = 3,
    top_keywords: int = 6,
    sample_labels: int = 4,
) -> list[WorkflowOpportunity]:
    """Cluster labelled sessions by project and rank automation opportunities.

    Only projects with at least ``min_sessions`` labelled sessions qualify —
    one-off work isn't worth automating. Returns highest-scoring first.
    """
    rows = conn.execute(
        """SELECT repo_root, cwd, label FROM sessions
           WHERE label IS NOT NULL AND label != ''""",
    ).fetchall()

    by_repo: dict[str, list[str]] = {}
    for row in rows:
        key = _repo_key(row)
        if not key:
            continue
        by_repo.setdefault(key, []).append(row["label"])

    opportunities: list[WorkflowOpportunity] = []
    for repo, labels in by_repo.items():
        if len(labels) < min_sessions:
            continue
        counter: Counter[str] = Counter()
        for label in labels:
            # Count each keyword once per label so a single verbose label
            # can't dominate the keyword ranking.
            counter.update(set(_tokenize(label)))
        opportunities.append(WorkflowOpportunity(
            repo=repo,
            session_count=len(labels),
            keywords=counter.most_common(top_keywords),
            sample_labels=labels[:sample_labels],
        ))

    opportunities.sort(key=lambda o: o.score, reverse=True)
    return opportunities
