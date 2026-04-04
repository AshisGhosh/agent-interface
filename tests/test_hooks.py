"""Tests for hook processing and installation."""

import json
import os

import pytest

from agent_interface.db import get_connection
from agent_interface.hooks import (
    HOOK_EVENTS,
    generate_hook_config,
    install_hooks,
    process_hook,
)
from agent_interface.models import Session
from agent_interface.registry import get_session, register_session


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("AGI_DB_PATH", db_path)
    return get_connection()


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """Redirect settings to a temp dir."""
    import agent_interface.hooks as hooks_mod

    fake_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr(hooks_mod, "SETTINGS_PATH", fake_path)
    return fake_path


# ── hook config generation ───────────────────────────────────────────────────


def test_generate_hook_config():
    config = generate_hook_config()
    for event in HOOK_EVENTS:
        assert event in config
        assert len(config[event]) == 1
        assert config[event][0]["hooks"][0]["command"] == "agi hook"


# ── hook installation ────────────────────────────────────────────────────────


def test_install_hooks_fresh(settings_dir):
    ok, msg = install_hooks()
    assert ok
    assert "installed" in msg.lower()

    settings = json.loads(settings_dir.read_text())
    assert "hooks" in settings
    for event in HOOK_EVENTS:
        assert event in settings["hooks"]


def test_install_hooks_preserves_other_settings(settings_dir):
    settings_dir.parent.mkdir(parents=True, exist_ok=True)
    settings_dir.write_text(json.dumps({"theme": "dark", "hooks": {}}))

    ok, _ = install_hooks()
    assert ok

    settings = json.loads(settings_dir.read_text())
    assert settings["theme"] == "dark"
    assert "SessionStart" in settings["hooks"]


def test_install_hooks_warns_on_conflict(settings_dir):
    settings_dir.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "other"}]}
            ]
        }
    }
    settings_dir.write_text(json.dumps(existing))

    ok, msg = install_hooks()
    assert ok
    assert "Replaced" in msg
    assert "SessionStart" in msg


# ── hook processing ──────────────────────────────────────────────────────────


def test_process_session_start_registers_new(conn):
    result = process_hook({
        "hook_event_name": "SessionStart",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "registered" in result

    s = get_session(conn, "abc123")
    assert s is not None
    assert s.state == "running"
    assert s.cwd == "/tmp/work"


def test_process_stop_sets_waiting(conn):
    register_session(conn, Session(id="abc123", state="running"))

    result = process_hook({
        "hook_event_name": "Stop",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "waiting_for_user" in result

    s = get_session(conn, "abc123")
    assert s.state == "waiting_for_user"


def test_process_session_end_sets_done(conn):
    register_session(conn, Session(id="abc123", state="running"))

    result = process_hook({
        "hook_event_name": "SessionEnd",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "done" in result

    s = get_session(conn, "abc123")
    assert s.state == "done"


def test_process_notification_sets_waiting(conn):
    register_session(conn, Session(id="abc123", state="running"))

    result = process_hook({
        "hook_event_name": "Notification",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "waiting_for_user" in result

    s = get_session(conn, "abc123")
    assert s.state == "waiting_for_user"


def test_post_tool_use_heartbeat_only(conn):
    register_session(conn, Session(id="abc123", state="running"))

    result = process_hook({
        "hook_event_name": "PostToolUse",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "heartbeat" in result

    s = get_session(conn, "abc123")
    assert s.state == "running"


def test_post_tool_use_updates_state_if_not_running(conn):
    register_session(conn, Session(id="abc123", state="waiting_for_user"))

    result = process_hook({
        "hook_event_name": "PostToolUse",
        "session_id": "abc123",
        "cwd": "/tmp/work",
    })
    assert "running" in result

    s = get_session(conn, "abc123")
    assert s.state == "running"


def test_process_unknown_event(conn):
    result = process_hook({
        "hook_event_name": "SomeUnknownEvent",
        "session_id": "abc123",
    })
    assert "ignored" in result


def test_process_no_session_id(conn):
    result = process_hook({
        "hook_event_name": "SessionStart",
    })
    assert "ignored" in result


def test_auto_register_on_stop(conn):
    """A Stop event for an unknown session should register + set waiting."""
    result = process_hook({
        "hook_event_name": "Stop",
        "session_id": "new-session",
        "cwd": "/tmp/new",
    })
    assert "registered" in result

    s = get_session(conn, "new-session")
    assert s is not None
    assert s.state == "waiting_for_user"


def test_adopt_scan_registered_session(conn, monkeypatch):
    """A hook event should adopt a scan-registered session matching by PID."""
    # Simulate a scan-registered session with a known PID.
    # Use current PID so the liveness check doesn't auto-close it.
    my_pid = os.getpid()
    register_session(conn, Session(id=f"myhost:{my_pid}", state="running", pid=my_pid))

    # Make os.getppid return the same PID.
    monkeypatch.setattr(os, "getppid", lambda: my_pid)

    result = process_hook({
        "hook_event_name": "Stop",
        "session_id": "claude-uuid-abc",
        "cwd": "/tmp/work",
    })
    assert "adopted" in result

    # Old ID should be gone, new ID should exist with updated state.
    assert get_session(conn, f"myhost:{my_pid}") is None
    s = get_session(conn, "claude-uuid-abc")
    assert s is not None
    assert s.state == "waiting_for_user"
    assert s.pid == my_pid
