"""Tests that hooks anchor sessions to the real agent process, not the
ephemeral shell that launches the hook.

This is the root-cause fix for three problems at once:
  - phantom sessions (a live agent recorded as 'done'),
  - missed agi labels (label resolution skips falsely-'done' sessions),
  - stale liveness (the stored pid dies the instant the launching shell exits).
"""

import os

import pytest

import agent_interface.hooks as hooks_mod
from agent_interface.db import get_connection
from agent_interface.hooks import _find_by_pid_ancestry, process_hook
from agent_interface.models import Session
from agent_interface.registry import get_session, register_session, update_state
from agent_interface.states import SessionState


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "test.db"))
    return get_connection()


@pytest.fixture
def fake_agent_pid(monkeypatch):
    """Pretend the resolved agent process is *this* (alive) test process, so
    liveness checks pass and we can assert the pid was anchored correctly."""
    monkeypatch.setattr(hooks_mod, "resolve_agent_pid", lambda _pid: os.getpid())
    return os.getpid()


def test_fresh_register_anchors_to_agent_pid(conn, fake_agent_pid):
    process_hook({
        "hook_event_name": "SessionStart",
        "session_id": "uuid-1",
        "cwd": "/tmp/work",
    })

    s = get_session(conn, "uuid-1")
    assert s is not None
    # Anchored to the live agent pid, not the ephemeral hook shell.
    assert s.pid == fake_agent_pid
    assert s.state == SessionState.RUNNING


def test_revives_phantom_done_session(conn, fake_agent_pid):
    """A session left 'done' with a dead pid by an old hook must come back to
    life when a new hook fires for it (the agent is clearly still running)."""
    register_session(conn, Session(id="uuid-2", state="done", pid=999_999, cwd="/tmp/w"))

    # A heartbeat-class event for a non-running session flips it back to running.
    result = process_hook({
        "hook_event_name": "PostToolUse",
        "session_id": "uuid-2",
        "cwd": "/tmp/w",
    })

    s = get_session(conn, "uuid-2")
    assert s.state == SessionState.RUNNING
    assert s.pid == fake_agent_pid  # re-anchored to the live process
    assert "running" in result


def test_dead_anchor_pid_is_reaped(conn, monkeypatch):
    """Sanity check the liveness contract: if the resolver can't find a live
    agent (returns None) we fall back to the hook pid, and a genuinely dead
    session is reaped on read."""
    monkeypatch.setattr(hooks_mod, "resolve_agent_pid", lambda _pid: None)
    register_session(conn, Session(id="uuid-3", state="running", pid=999_999))

    s = get_session(conn, "uuid-3")
    assert s.state == SessionState.DONE  # _maybe_reap closed the dead pid


# ── missed agi labels ─────────────────────────────────────────────────────────


def test_label_resolution_blocked_by_phantom_then_fixed(conn, fake_agent_pid):
    """The phantom bug is what *caused* `agi label` / label_session to fail:
    a falsely-'done' session is invisible to ancestry resolution. After a hook
    revives it, resolution (and therefore labeling) works again."""
    # Phantom: agent is alive (pid == us) but the row says done.
    register_session(conn, Session(id="uuid-4", state="done", pid=os.getpid()))

    # While 'done', ancestry resolution skips it → label would fail.
    assert _find_by_pid_ancestry(conn, os.getpid()) is None

    # A hook fires (agent is active) → session revives.
    process_hook({
        "hook_event_name": "PostToolUse",
        "session_id": "uuid-4",
        "cwd": "/tmp/w",
    })

    # Now resolvable → labeling can find the session.
    match = _find_by_pid_ancestry(conn, os.getpid())
    assert match is not None and match.id == "uuid-4"


def test_label_resolution_finds_live_session(conn):
    """`agi label` walks from the current pid to a registered agent session."""
    register_session(conn, Session(id="uuid-5", state="running", pid=os.getpid()))
    update_state(conn, "uuid-5", "running")

    match = _find_by_pid_ancestry(conn, os.getpid())
    assert match is not None
    assert match.id == "uuid-5"
