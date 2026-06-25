"""Tests for registry.reconcile — guards against stale processes and phantom
('running' but actually dead) sessions, including pid reuse."""

import agent_interface.registry as reg_mod
import agent_interface.scan as scan_mod
from agent_interface.models import Session
from agent_interface.registry import get_session, reconcile, register_session
from agent_interface.states import SessionState


def _running(id: str = "s1", pid: int = 4242) -> Session:
    return Session(id=id, state="running", pid=pid, cwd="/tmp/x")


def test_reaps_exited_process(conn, monkeypatch):
    register_session(conn, _running())
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: False)

    summary = reconcile(conn)

    assert summary["reaped_exited"] == 1
    assert summary["reaped_reused"] == 0
    assert get_session(conn, "s1").state == SessionState.DONE


def test_reaps_pid_reuse(conn, monkeypatch):
    """Process is alive but it's no longer the agent — a recycled pid."""
    register_session(conn, _running())
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: False)

    summary = reconcile(conn)

    assert summary["reaped_reused"] == 1
    assert summary["reaped_exited"] == 0
    assert get_session(conn, "s1").state == SessionState.DONE


def test_keeps_live_agent(conn, monkeypatch):
    register_session(conn, _running())
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: True)

    summary = reconcile(conn)

    assert summary["reaped_exited"] == summary["reaped_reused"] == 0
    assert get_session(conn, "s1").state == SessionState.RUNNING


def test_unknown_identity_is_conservative(conn, monkeypatch):
    """If identity can't be determined (no /proc), don't reap a live pid."""
    register_session(conn, _running())
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: None)

    reconcile(conn)

    assert get_session(conn, "s1").state == SessionState.RUNNING


def test_skips_pidless_sessions(conn, monkeypatch):
    register_session(conn, Session(id="s1", state="running", pid=None))
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: False)

    summary = reconcile(conn)

    assert summary["checked"] == 0
    assert get_session(conn, "s1").state == SessionState.RUNNING


def test_ignores_terminal_sessions(conn, monkeypatch):
    register_session(conn, _running("done1"))
    reg_mod.update_state(conn, "done1", "done")
    # _pid_alive would say dead, but a done session must be left alone.
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: False)

    summary = reconcile(conn)

    assert summary["checked"] == 0
    assert summary["reaped_exited"] == 0


def test_reconcile_records_event(conn, monkeypatch):
    register_session(conn, _running())
    monkeypatch.setattr(reg_mod, "_pid_alive", lambda pid: False)

    reconcile(conn)

    events = [e.event_type for e in reg_mod.list_events(conn, "s1")]
    assert "auto_closed" in events
