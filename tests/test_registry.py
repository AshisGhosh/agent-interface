"""Tests for the session registry."""

from agent_interface.models import Session
from agent_interface.registry import (
    archive_session,
    get_session,
    list_events,
    list_sessions,
    list_waiting,
    register_session,
    rename_session,
    restore_session,
    update_state,
)
from agent_interface.states import SessionState


def _make_session(id: str = "test-1", **kwargs) -> Session:
    defaults = dict(state="running", host="localhost", cwd="/tmp/test")
    defaults.update(kwargs)
    return Session(id=id, **defaults)


def test_register_and_get(conn):
    s = _make_session()
    register_session(conn, s)
    got = get_session(conn, "test-1")
    assert got is not None
    assert got.id == "test-1"
    assert got.state == "running"
    assert got.host == "localhost"


def test_register_creates_event(conn):
    register_session(conn, _make_session())
    events = list_events(conn, "test-1")
    assert len(events) == 1
    assert events[0].event_type == "session_registered"


def test_list_sessions_excludes_archived(conn):
    register_session(conn, _make_session("s1"))
    register_session(conn, _make_session("s2"))
    archive_session(conn, "s2")

    active = list_sessions(conn)
    ids = [s.id for s in active]
    assert "s1" in ids
    assert "s2" not in ids


def test_list_sessions_excludes_done(conn):
    register_session(conn, _make_session("s1"))
    register_session(conn, _make_session("s2"))
    update_state(conn, "s2", "done")

    active = list_sessions(conn)
    ids = [s.id for s in active]
    assert "s1" in ids
    assert "s2" not in ids


def test_list_sessions_all(conn):
    register_session(conn, _make_session("s1"))
    register_session(conn, _make_session("s2"))
    archive_session(conn, "s2")

    all_sessions = list_sessions(conn, include_archived=True, include_done=True)
    ids = [s.id for s in all_sessions]
    assert "s1" in ids
    assert "s2" in ids


def test_list_waiting(conn):
    register_session(conn, _make_session("s1", state="running"))
    register_session(conn, _make_session("s2", state="waiting_for_user"))
    register_session(conn, _make_session("s3", state="blocked"))

    waiting = list_waiting(conn)
    assert len(waiting) == 1
    assert waiting[0].id == "s2"


def test_update_state(conn):
    register_session(conn, _make_session())
    s = update_state(conn, "test-1", "waiting_for_user")
    assert s is not None
    assert s.state == "waiting_for_user"

    events = list_events(conn, "test-1")
    types = [e.event_type for e in events]
    assert "state_changed" in types


def test_update_state_invalid(conn):
    register_session(conn, _make_session())
    import pytest
    with pytest.raises(ValueError):
        update_state(conn, "test-1", "nonexistent_state")


def test_update_state_unknown_session(conn):
    result = update_state(conn, "no-such-id", "running")
    assert result is None


def test_rename(conn):
    register_session(conn, _make_session())
    s = rename_session(conn, "test-1", "my-task")
    assert s is not None
    assert s.label == "my-task"


def test_rename_unknown_session(conn):
    result = rename_session(conn, "no-such-id", "label")
    assert result is None


def test_archive_and_restore(conn):
    register_session(conn, _make_session())

    s = archive_session(conn, "test-1")
    assert s is not None
    assert s.state == SessionState.ARCHIVED
    assert s.archived_at is not None

    s = restore_session(conn, "test-1")
    assert s is not None
    assert s.state == SessionState.IDLE
    assert s.archived_at is None


def test_archive_unknown_session(conn):
    result = archive_session(conn, "no-such-id")
    assert result is None


def test_get_unknown_session(conn):
    result = get_session(conn, "no-such-id")
    assert result is None


def test_partial_metadata(conn):
    """Sessions with minimal metadata should work fine."""
    s = Session(id="minimal")
    register_session(conn, s)
    got = get_session(conn, "minimal")
    assert got is not None
    assert got.host is None
    assert got.cwd is None
    assert got.state == "unknown"
