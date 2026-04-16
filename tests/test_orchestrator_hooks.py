"""Tests for orchestrator SessionStart / SessionEnd hook integration."""


import pytest

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.orchestrator.hooks import on_session_end, on_session_start
from agent_interface.orchestrator.states import TaskStatus


@pytest.fixture
def oconn(conn, monkeypatch):
    ensure_schema(conn)
    # Route orchestrator.db.get_connection() to the test conn.
    from agent_interface.orchestrator import db as orch_db
    from agent_interface.orchestrator import hooks as orch_hooks
    monkeypatch.setattr(orch_db, "get_connection", lambda: conn)
    monkeypatch.setattr(orch_hooks, "get_connection", lambda: conn)
    return conn


# ── SessionStart ─────────────────────────────────────────────────────────────

def test_session_start_no_task_returns_none(oconn):
    assert on_session_start("sess-1", "/tmp") is None


def test_session_start_with_env_task_binds(oconn, monkeypatch):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "do the work")
    monkeypatch.setenv("AGI_TASK_ID", t.id)

    result = on_session_start("sess-1", "/tmp")

    assert result is not None
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert t.id in result["hookSpecificOutput"]["additionalContext"]
    assert "do the work" in result["hookSpecificOutput"]["additionalContext"]

    # Task should now be bound + in_progress.
    updated = core.get_task(oconn, t.id)
    assert updated.assigned_session_id == "sess-1"
    assert updated.status == TaskStatus.IN_PROGRESS


def test_session_start_finds_already_assigned_task(oconn):
    core.create_project(oconn, "p1")
    core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")

    result = on_session_start("sess-1", "/tmp")
    assert result is not None
    assert "Your assignment" in result["hookSpecificOutput"]["additionalContext"]


def test_session_start_is_silent_on_error(oconn, monkeypatch):
    """Never raise; hook pipeline must keep working."""
    def boom():
        raise RuntimeError("db down")
    from agent_interface.orchestrator import hooks as h
    monkeypatch.setattr(h, "get_connection", boom)
    assert on_session_start("sess-1", "/tmp") is None


# ── SessionEnd ───────────────────────────────────────────────────────────────

def test_session_end_blocks_open_task(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")

    on_session_end("sess-1")

    updated = core.get_task(oconn, t.id)
    assert updated.status == TaskStatus.BLOCKED

    events = [e.event_type for e in core.list_events(oconn, t.id)]
    assert "blocked" in events


def test_session_end_ignores_done_tasks(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")
    core.done_task(oconn, t.id, summary="ok", actor="session:sess-1")

    on_session_end("sess-1")

    updated = core.get_task(oconn, t.id)
    assert updated.status == TaskStatus.DONE


def test_session_end_skips_already_blocked(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    core.claim_next(oconn, "sess-1", project="p1")
    core.block_task(oconn, t.id, "manual")

    before_events = len(core.list_events(oconn, t.id))
    on_session_end("sess-1")
    after_events = len(core.list_events(oconn, t.id))
    assert before_events == after_events  # no duplicate block event


def test_session_end_silent_without_assignment(oconn):
    # no task bound — must not raise
    on_session_end("sess-nope")
