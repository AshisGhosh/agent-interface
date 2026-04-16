"""Tests for auto-reap and auto-promote behavior."""

import pytest

from agent_interface.models import Session
from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.orchestrator.states import TaskStatus
from agent_interface.registry import register_session


@pytest.fixture
def oconn(conn):
    ensure_schema(conn)
    return conn


# ── auto-promote ─────────────────────────────────────────────────────────────

def test_done_auto_promotes_dependents(oconn):
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "first")
    b = core.add_task(oconn, "p1", "second", depends_on=[a.id])

    assert b.status == TaskStatus.BACKLOG
    core.done_task(oconn, a.id, summary="ok")

    b_after = core.get_task(oconn, b.id)
    assert b_after.status == TaskStatus.READY

    # And a 'ready' event with a trigger payload exists.
    events = [e for e in core.list_events(oconn, b.id) if e.event_type == "ready"]
    assert len(events) == 1
    assert a.id in events[0].payload_json


def test_auto_promote_waits_for_all_deps(oconn):
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "a")
    b = core.add_task(oconn, "p1", "b")
    c = core.add_task(oconn, "p1", "c", depends_on=[a.id, b.id])

    core.done_task(oconn, a.id, summary="ok")
    assert core.get_task(oconn, c.id).status == TaskStatus.BACKLOG

    core.done_task(oconn, b.id, summary="ok")
    assert core.get_task(oconn, c.id).status == TaskStatus.READY


def test_auto_promote_skips_agent_created(oconn):
    """Agent-created tasks require user triage — no auto-promote."""
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "a")
    b = core.add_task(
        oconn, "p1", "b",
        depends_on=[a.id],
        creator="session:xyz",
    )

    core.done_task(oconn, a.id, summary="ok")

    b_after = core.get_task(oconn, b.id)
    assert b_after.status == TaskStatus.BACKLOG  # still in backlog


# ── auto-reap ────────────────────────────────────────────────────────────────

def _register_dead_session(conn, sid: str, pid: int | None = None):
    """Register a session whose pid is definitely dead (pid=99999999)."""
    sess = Session(id=sid, state="running", pid=pid)
    register_session(conn, sess)


def test_reap_orphans_with_missing_session(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    # Manually bind to a nonexistent session.
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "ghost-session", t.id),
    )
    oconn.commit()

    reaped = core.reap_orphaned_tasks(oconn)
    assert t.id in reaped

    after = core.get_task(oconn, t.id)
    assert after.status == TaskStatus.READY
    assert after.assigned_session_id is None


def test_reap_spares_pidless_running_session(oconn):
    """A newly-dispatched session has no pid until SessionStart fires.

    Reap must not kill it just because pid is None — assume alive.
    """
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    _register_dead_session(oconn, "fresh-sess", pid=None)
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "fresh-sess", t.id),
    )
    oconn.commit()

    reaped = core.reap_orphaned_tasks(oconn)
    assert t.id not in reaped
    assert core.get_task(oconn, t.id).status == TaskStatus.IN_PROGRESS


def test_reap_does_not_rely_on_pid_liveness(oconn):
    """Auto-reap trusts the SessionEnd hook, not pid checks.

    Claude Code spawns short-lived sub-processes whose pids exit quickly
    while the main session continues — pid-based reap would wrongly clear
    active task bindings. So a dead-pid session is *not* reaped; only
    explicit done/archived state triggers cleanup.
    """
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    _register_dead_session(oconn, "sess-dead-pid", pid=99999999)
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "sess-dead-pid", t.id),
    )
    oconn.commit()

    reaped = core.reap_orphaned_tasks(oconn)
    assert t.id not in reaped

    # But once the session is marked done (SessionEnd fired), it IS reaped.
    oconn.execute("UPDATE sessions SET state='done' WHERE id=?", ("sess-dead-pid",))
    oconn.commit()
    reaped = core.reap_orphaned_tasks(oconn)
    assert t.id in reaped


def test_reap_spares_live_sessions(oconn):
    """A task whose session is in running state should not be reaped."""
    import os
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    _register_dead_session(oconn, "sess-alive", pid=os.getpid())
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "sess-alive", t.id),
    )
    oconn.commit()

    reaped = core.reap_orphaned_tasks(oconn)
    assert t.id not in reaped
    assert core.get_task(oconn, t.id).status == TaskStatus.IN_PROGRESS


def test_reap_runs_on_list_tasks(oconn):
    """list_tasks should auto-reap by default."""
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "ghost", t.id),
    )
    oconn.commit()

    # Listing triggers reap.
    tasks = core.list_tasks(oconn, project="p1")
    assert tasks[0].status == TaskStatus.READY


def test_reap_orphan_appends_event(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    oconn.execute(
        "UPDATE tasks SET status=?, assigned_session_id=? WHERE id=?",
        ("in_progress", "ghost", t.id),
    )
    oconn.commit()

    core.reap_orphaned_tasks(oconn)
    events = core.list_events(oconn, t.id)
    assert any(e.event_type == "session_orphaned" for e in events)
