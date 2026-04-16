"""Tests for the orchestrator core."""

import pytest

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.orchestrator.states import TaskStatus


@pytest.fixture
def oconn(conn):
    """Session-registry conn with orchestrator tables layered on."""
    ensure_schema(conn)
    return conn


# ── projects ─────────────────────────────────────────────────────────────────

def test_create_and_list_project(oconn):
    p = core.create_project(oconn, "game-dev", description="ship it")
    assert p.id.startswith("p-")
    assert p.name == "game-dev"

    projs = core.list_projects(oconn)
    assert len(projs) == 1
    assert projs[0].name == "game-dev"


def test_project_name_unique(oconn):
    core.create_project(oconn, "llm-sft")
    with pytest.raises(Exception):
        core.create_project(oconn, "llm-sft")


# ── tasks: creation ──────────────────────────────────────────────────────────

def test_add_task_user_defaults_to_ready(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "do the thing")
    assert t.status == TaskStatus.READY
    assert t.creator == "user"
    assert t.priority == 2


def test_add_task_agent_defaults_to_backlog(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "discovered bug", creator="session:abc")
    assert t.status == TaskStatus.BACKLOG


def test_add_task_with_deps_and_tags(oconn):
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "first")
    b = core.add_task(oconn, "p1", "second", depends_on=[a.id], tags=["train", "p0"])
    assert b.depends_on == [a.id]
    assert set(b.tags) == {"train", "p0"}


def test_add_task_unknown_project(oconn):
    with pytest.raises(ValueError):
        core.add_task(oconn, "nope", "x")


def test_add_task_creates_event(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    events = core.list_events(oconn, t.id)
    assert len(events) == 1
    assert events[0].event_type == "created"


# ── tasks: listing ───────────────────────────────────────────────────────────

def test_list_tasks_filters_closed_by_default(oconn):
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "a")
    b = core.add_task(oconn, "p1", "b")
    core.done_task(oconn, b.id, summary="shipped")

    open_only = core.list_tasks(oconn, project="p1")
    assert [t.id for t in open_only] == [a.id]

    all_tasks = core.list_tasks(oconn, project="p1", include_closed=True)
    assert {t.id for t in all_tasks} == {a.id, b.id}


def test_list_tasks_orders_by_priority(oconn):
    core.create_project(oconn, "p1")
    core.add_task(oconn, "p1", "low", priority=3)
    core.add_task(oconn, "p1", "high", priority=0)
    core.add_task(oconn, "p1", "mid", priority=2)

    tasks = core.list_tasks(oconn, project="p1")
    assert [t.title for t in tasks] == ["high", "mid", "low"]


# ── claiming ─────────────────────────────────────────────────────────────────

def test_claim_next_picks_highest_priority(oconn):
    core.create_project(oconn, "p1")
    core.add_task(oconn, "p1", "low", priority=3)
    high = core.add_task(oconn, "p1", "high", priority=0)
    core.add_task(oconn, "p1", "mid", priority=2)

    claimed = core.claim_next(oconn, "sess-1", project="p1")
    assert claimed is not None
    assert claimed.id == high.id
    assert claimed.status == TaskStatus.IN_PROGRESS
    assert claimed.assigned_session_id == "sess-1"


def test_claim_next_skips_non_ready(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "agent-made", creator="session:x")  # backlog
    assert t.status == TaskStatus.BACKLOG

    claimed = core.claim_next(oconn, "sess-1", project="p1")
    assert claimed is None


def test_claim_next_filters_by_tag(oconn):
    core.create_project(oconn, "p1")
    core.add_task(oconn, "p1", "infra", tags=["infra"], priority=0)
    train = core.add_task(oconn, "p1", "train", tags=["train"], priority=1)

    claimed = core.claim_next(oconn, "sess-1", project="p1", tags=["train"])
    assert claimed is not None
    assert claimed.id == train.id


def test_claim_next_is_idempotent_once_taken(oconn):
    core.create_project(oconn, "p1")
    core.add_task(oconn, "p1", "solo")

    first = core.claim_next(oconn, "sess-1", project="p1")
    assert first is not None

    second = core.claim_next(oconn, "sess-2", project="p1")
    assert second is None  # nothing left ready


def test_claim_next_no_projects_returns_none(oconn):
    out = core.claim_next(oconn, "sess-1", project="nope")
    assert out is None


# ── progress / block / done ──────────────────────────────────────────────────

def test_progress_appends_event(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.progress(oconn, t.id, "step 1", pct=25, actor="session:abc")

    events = [e.event_type for e in core.list_events(oconn, t.id)]
    assert events == ["created", "progress"]


def test_block_and_unblock(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")

    blocked = core.block_task(oconn, t.id, "needs API key", needs="user")
    assert blocked.status == TaskStatus.BLOCKED

    unblocked = core.unblock_task(oconn, t.id)
    assert unblocked.status == TaskStatus.IN_PROGRESS  # had an assignee


def test_block_unassigned_unblocks_to_ready(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.block_task(oconn, t.id, "r")
    out = core.unblock_task(oconn, t.id)
    assert out.status == TaskStatus.READY


def test_block_rejects_invalid_needs(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    with pytest.raises(ValueError):
        core.block_task(oconn, t.id, "r", needs="bogus")


def test_done_closes_task(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    done = core.done_task(oconn, t.id, summary="ok")
    assert done.status == TaskStatus.DONE
    assert done.closed_at is not None


def test_reopen(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.done_task(oconn, t.id, summary="ok")
    reopened = core.reopen_task(oconn, t.id)
    assert reopened.status == TaskStatus.READY
    assert reopened.closed_at is None


def test_reopen_requires_done(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    with pytest.raises(ValueError):
        core.reopen_task(oconn, t.id)


# ── deps + promotion ─────────────────────────────────────────────────────────

def test_promote_requires_deps_done(oconn):
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "a")
    b = core.add_task(
        oconn, "p1", "b",
        depends_on=[a.id],
        creator="session:x",  # forces backlog so promote is meaningful
    )
    with pytest.raises(ValueError):
        core.promote(oconn, b.id)

    core.done_task(oconn, a.id, summary="ok")
    promoted = core.promote(oconn, b.id)
    assert promoted.status == TaskStatus.READY


def test_promote_rejects_non_backlog(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")  # lands ready
    with pytest.raises(ValueError):
        core.promote(oconn, t.id)


# ── event log is append-only and ordered ─────────────────────────────────────

def test_event_log_captures_lifecycle(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")
    core.progress(oconn, t.id, "halfway")
    core.block_task(oconn, t.id, "stuck")
    core.unblock_task(oconn, t.id)
    core.done_task(oconn, t.id, summary="done")

    types = [e.event_type for e in core.list_events(oconn, t.id)]
    assert types == [
        "created", "claimed", "progress", "blocked", "unblocked", "done",
    ]
