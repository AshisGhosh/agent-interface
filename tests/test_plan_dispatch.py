"""Tests for plan_project and dispatch logic."""

import pytest

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.orchestrator.states import TaskStatus


@pytest.fixture
def oconn(conn):
    ensure_schema(conn)
    return conn


# ── plan_project ─────────────────────────────────────────────────────────────

def test_plan_creates_project_and_tasks(oconn):
    proj, tasks = core.plan_project(
        oconn,
        "training",
        "LLM fine-tuning",
        [
            {"title": "set up infra", "priority": 0, "tags": ["infra"]},
            {"title": "prep data", "priority": 1, "depends_on": ["set up infra"]},
            {"title": "sweep lr", "priority": 2, "tags": ["train"], "depends_on": ["prep data"]},
            {"title": "sweep arch", "priority": 2, "tags": ["train"], "depends_on": ["prep data"]},
        ],
    )

    assert proj.name == "training"
    assert len(tasks) == 4

    # First task has no deps → ready.
    assert tasks[0].title == "set up infra"
    assert tasks[0].status == TaskStatus.READY

    # Others have deps → backlog.
    assert tasks[1].status == TaskStatus.BACKLOG
    assert tasks[2].status == TaskStatus.BACKLOG
    assert tasks[3].status == TaskStatus.BACKLOG

    # Deps are resolved by title.
    assert tasks[1].depends_on == [tasks[0].id]
    assert tasks[2].depends_on == [tasks[1].id]


def test_plan_multiple_roots_all_ready(oconn):
    _, tasks = core.plan_project(
        oconn,
        "parallel",
        "independent tasks",
        [
            {"title": "a"},
            {"title": "b"},
            {"title": "c"},
        ],
    )
    assert all(t.status == TaskStatus.READY for t in tasks)


def test_plan_with_descriptions(oconn):
    _, tasks = core.plan_project(
        oconn,
        "described",
        "has descs",
        [{"title": "x", "description": "detailed markdown here"}],
    )
    assert tasks[0].description == "detailed markdown here"


def test_plan_unknown_dep_treated_as_dep(oconn):
    """If a dep title doesn't match, treat the task as having an unresolvable dep → backlog."""
    _, tasks = core.plan_project(
        oconn,
        "broken-dep",
        "has bad ref",
        [{"title": "orphan", "depends_on": ["nonexistent"]}],
    )
    assert tasks[0].status == TaskStatus.BACKLOG


def test_plan_events_created_for_each_task(oconn):
    _, tasks = core.plan_project(
        oconn, "evented", "test",
        [{"title": "a"}, {"title": "b"}],
    )
    for t in tasks:
        events = core.list_events(oconn, t.id)
        assert len(events) >= 1
        assert events[0].event_type == "created"


# ── plan → claim → done flow ────────────────────────────────────────────────

def test_plan_then_claim_flow(oconn):
    """Full lifecycle: plan → claim ready → done → promote deps → claim again."""
    _, tasks = core.plan_project(
        oconn,
        "lifecycle",
        "test",
        [
            {"title": "first", "priority": 0},
            {"title": "second", "priority": 1, "depends_on": ["first"]},
        ],
    )

    # Only first is claimable.
    claimed = core.claim_next(oconn, "sess-1", project="lifecycle")
    assert claimed is not None
    assert claimed.title == "first"

    # Nothing else to claim.
    assert core.claim_next(oconn, "sess-2", project="lifecycle") is None

    # Finish first.
    core.done_task(oconn, claimed.id, summary="done")

    # Promote second (dep now done).
    second = tasks[1]
    promoted = core.promote(oconn, second.id)
    assert promoted.status == TaskStatus.READY

    # Now second is claimable.
    claimed2 = core.claim_next(oconn, "sess-2", project="lifecycle")
    assert claimed2 is not None
    assert claimed2.title == "second"
