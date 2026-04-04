"""Tests for the Telegram bot integration."""

import json
import os

import pytest

from agent_interface.db import get_connection
from agent_interface.models import Session
from agent_interface.registry import register_session


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("AGI_DB_PATH", db_path)
    return get_connection()


@pytest.fixture
def tg(tmp_path, monkeypatch):
    """Set up Telegram module with fake config and mocked API."""
    import agent_interface.telegram as tg_mod

    # Fake config.
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "telegram_bot_token": "fake-token",
        "telegram_chat_id": 12345,
    }))
    monkeypatch.setattr(tg_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(tg_mod, "DASHBOARD_PATH", tmp_path / "dashboard.json")
    monkeypatch.setattr(tg_mod, "PIDFILE_PATH", tmp_path / "bot.pid")
    monkeypatch.setattr(tg_mod, "BOT_LOG_PATH", tmp_path / "bot.log")

    # Track API calls.
    tg_mod._test_api_calls = []
    tg_mod._test_api_responses = {}

    def fake_api(token, method, data, timeout=15):
        tg_mod._test_api_calls.append((method, data))
        resp = tg_mod._test_api_responses.get(method, {"ok": True, "result": {}})
        # sendMessage needs a message_id in result.
        if method == "sendMessage" and "message_id" not in resp.get("result", {}):
            resp = {"ok": True, "result": {"message_id": 999}}
        return resp

    monkeypatch.setattr(tg_mod, "_api", fake_api)
    return tg_mod


# ── formatting ───────────────────────────────────────────────────────────────


def test_format_agent_message_code_block(tg):
    result = tg._format_agent_message("before\n```python\nprint('hi')\n```\nafter")
    assert "<pre><code>" in result
    assert "print('hi')" in result


def test_format_agent_message_inline_code(tg):
    result = tg._format_agent_message("use `foo()` here")
    assert "<code>foo()</code>" in result


def test_format_agent_message_bold(tg):
    result = tg._format_agent_message("this is **important**")
    assert "<b>important</b>" in result


def test_format_agent_message_plain(tg):
    result = tg._format_agent_message("just plain text")
    assert result == "just plain text"


def test_compact_cwd(tg, monkeypatch):
    monkeypatch.setenv("HOME", "/home/testuser")
    assert tg._compact_cwd("/home/testuser/code/foo") == "~/code/foo"
    assert tg._compact_cwd("/opt/other") == "/opt/other"


# ── send_message ─────────────────────────────────────────────────────────────


def test_send_message(tg):
    ok = tg.send_message("hello")
    assert ok
    assert len(tg._test_api_calls) == 1
    method, data = tg._test_api_calls[0]
    assert method == "sendMessage"
    assert data["text"] == "hello"
    assert data["parse_mode"] == "HTML"


def test_send_message_with_reply_markup(tg):
    markup = {"inline_keyboard": [[{"text": "Go", "callback_data": "go"}]]}
    tg.send_message("test", reply_markup=markup)
    _, data = tg._test_api_calls[0]
    assert data["reply_markup"] == markup


def test_send_message_falls_back_on_html_error(tg):
    call_count = [0]
    original_calls = tg._test_api_calls

    def fail_then_succeed(token, method, data, timeout=15):
        call_count[0] += 1
        original_calls.append((method, data))
        if call_count[0] == 1:
            return {"ok": False}
        return {"ok": True, "result": {"message_id": 999}}

    tg._api = fail_then_succeed

    ok = tg.send_message("<bad>html")
    assert ok
    assert len(original_calls) == 2
    # Second call should have empty parse_mode.
    _, retry_data = original_calls[1]
    assert retry_data["parse_mode"] == ""


def test_send_message_no_config(tg):
    tg.CONFIG_PATH.write_text("{}")
    ok = tg.send_message("hello")
    assert not ok


# ── notify_waiting ───────────────────────────────────────────────────────────


def test_notify_waiting(tg, conn):
    register_session(conn, Session(
        id="s1", state="waiting_for_user", cwd="/tmp/proj", label="my task",
    ))

    ok = tg.notify_waiting("s1", "Need your input on X")
    assert ok
    _, data = tg._test_api_calls[0]
    assert "my task" in data["text"]
    assert "Need your input on X" in data["text"]
    assert "reply_markup" in data


def test_notify_waiting_no_label_uses_dirname(tg, conn):
    register_session(conn, Session(id="s1", state="waiting_for_user", cwd="/tmp/my-project"))

    tg.notify_waiting("s1")
    _, data = tg._test_api_calls[0]
    assert "my-project" in data["text"]


def test_notify_waiting_unknown_session(tg, conn):
    ok = tg.notify_waiting("nonexistent")
    assert not ok


def test_notify_waiting_truncates_long_message(tg, conn):
    register_session(conn, Session(id="s1", state="waiting_for_user", cwd="/tmp"))

    long_msg = "x" * 5000
    tg.notify_waiting("s1", long_msg)
    _, data = tg._test_api_calls[0]
    assert len(data["text"]) < 4096


# ── dashboard ────────────────────────────────────────────────────────────────


def test_build_dashboard_empty(tg, conn):
    text = tg._build_dashboard_text()
    assert "No active sessions" in text


def test_build_dashboard_with_sessions(tg, conn):
    register_session(conn, Session(id="s1", state="running", cwd="/tmp/a", label="task A"))
    register_session(conn, Session(
        id="s2", state="waiting_for_user", cwd="/tmp/b", label="task B",
    ))

    text = tg._build_dashboard_text()
    assert "RUNNING" in text
    assert "WAITING" in text
    assert "task A" in text
    assert "task B" in text


def test_update_dashboard_creates_new(tg, conn):
    register_session(conn, Session(id="s1", state="running", cwd="/tmp"))

    ok = tg.update_dashboard()
    assert ok
    # Should have called sendMessage and pinChatMessage.
    methods = [m for m, _ in tg._test_api_calls]
    assert "sendMessage" in methods
    assert "pinChatMessage" in methods

    # State file should have message_id.
    state = tg._load_dashboard_state()
    assert state.get("message_id") == 999


def test_update_dashboard_edits_existing(tg, conn):
    register_session(conn, Session(id="s1", state="running", cwd="/tmp"))

    # Set up existing dashboard state.
    tg._save_dashboard_state({"message_id": 42, "last_updated": 0})

    ok = tg.update_dashboard()
    assert ok
    method, data = tg._test_api_calls[0]
    assert method == "editMessageText"
    assert data["message_id"] == 42


def test_update_dashboard_throttled(tg, conn):
    import time
    register_session(conn, Session(id="s1", state="running", cwd="/tmp"))

    tg._save_dashboard_state({"message_id": 42, "last_updated": time.time()})

    ok = tg.update_dashboard()
    assert not ok  # Throttled.
    assert len(tg._test_api_calls) == 0


# ── callback handling ────────────────────────────────────────────────────────


def test_handle_callback_reply(tg, conn):
    register_session(conn, Session(
        id="s1", state="waiting_for_user", pid=os.getpid(), label="my task",
    ))

    tg._handle_callback("fake-token", {
        "id": "cb1",
        "data": "reply:s1",
    })

    # Should have answered callback + sent a message.
    methods = [m for m, _ in tg._test_api_calls]
    assert "answerCallbackQuery" in methods
    assert "sendMessage" in methods

    # Reply target should be set.
    target = tg._get_reply_target()
    assert target is not None
    assert target["session_id"] == "s1"


def test_handle_callback_reply_unknown_session(tg, conn):
    tg._handle_callback("fake-token", {
        "id": "cb1",
        "data": "reply:nonexistent",
    })

    methods = [m for m, _ in tg._test_api_calls]
    assert "answerCallbackQuery" in methods


# ── at-reply parsing ─────────────────────────────────────────────────────────


def test_handle_at_reply(tg, conn, monkeypatch):
    register_session(conn, Session(
        id="s1", state="waiting_for_user", pid=os.getpid(),
        label="my task", tmux_session="0", tmux_window="0", tmux_pane="0",
    ))

    # Mock tmux resolution and send.
    monkeypatch.setattr(tg, "_resolve_tmux_target", lambda pid: "0:0.0")
    monkeypatch.setattr(tg, "send_to_tmux", lambda t, m: (True, ""))

    tg._handle_at_reply("@my test message")

    # Should have sent confirmation.
    messages = [d["text"] for m, d in tg._test_api_calls if m == "sendMessage"]
    assert any("my task" in msg for msg in messages)


def test_handle_at_reply_quoted_query(tg, conn, monkeypatch):
    register_session(conn, Session(
        id="s1", state="waiting_for_user", pid=os.getpid(),
        label="my long task name", tmux_session="0", tmux_window="0", tmux_pane="0",
    ))

    monkeypatch.setattr(tg, "_resolve_tmux_target", lambda pid: "0:0.0")
    monkeypatch.setattr(tg, "send_to_tmux", lambda t, m: (True, ""))

    tg._handle_at_reply('@"my long" test message')

    messages = [d["text"] for m, d in tg._test_api_calls if m == "sendMessage"]
    assert any("my long task name" in msg for msg in messages)


def test_handle_at_reply_no_match(tg, conn):
    tg._handle_at_reply("@nonexistent hello")

    messages = [d["text"] for m, d in tg._test_api_calls if m == "sendMessage"]
    assert any("No session" in msg for msg in messages)


# ── reply target ─────────────────────────────────────────────────────────────


def test_reply_target_set_and_get(tg, tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_REPLY_TARGET_PATH", tmp_path / "reply.json")

    tg._set_reply_target("s1", 1234, "my task")
    target = tg._get_reply_target()
    assert target is not None
    assert target["session_id"] == "s1"
    assert target["pid"] == 1234


def test_reply_target_expires(tg, tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(tg, "_REPLY_TARGET_PATH", tmp_path / "reply.json")

    tg._set_reply_target("s1", 1234, "my task")

    # Manually expire it.
    data = json.loads((tmp_path / "reply.json").read_text())
    data["timestamp"] = time.time() - 700  # > 600s expiry
    (tmp_path / "reply.json").write_text(json.dumps(data))

    target = tg._get_reply_target()
    assert target is None


# ── transcript reading ───────────────────────────────────────────────────────


def test_read_last_agent_message(tg, tmp_path):
    transcript = tmp_path / "test.jsonl"
    transcript.write_text(
        '{"role":"user","content":"hello"}\n'
        '{"role":"assistant","content":"first response"}\n'
        '{"role":"user","content":"thanks"}\n'
        '{"role":"assistant","content":"second response"}\n'
    )
    result = tg._read_last_agent_message(str(transcript))
    assert result == "second response"


def test_read_last_agent_message_with_content_list(tg, tmp_path):
    transcript = tmp_path / "test.jsonl"
    transcript.write_text(
        '{"role":"assistant","content":[{"type":"text","text":"hello from list"}]}\n'
    )
    result = tg._read_last_agent_message(str(transcript))
    assert result == "hello from list"


def test_read_last_agent_message_skips_empty(tg, tmp_path):
    transcript = tmp_path / "test.jsonl"
    transcript.write_text(
        '{"role":"assistant","content":"good response"}\n'
        '{"role":"assistant","content":""}\n'
        '{"role":"system","content":"system msg"}\n'
    )
    result = tg._read_last_agent_message(str(transcript))
    assert result == "good response"


def test_read_last_agent_message_missing_file(tg):
    result = tg._read_last_agent_message("/nonexistent/path.jsonl")
    assert result is None


def test_find_transcript(tg, tmp_path, monkeypatch):
    # Create a fake transcript.
    projects = tmp_path / ".claude" / "projects" / "test-project"
    projects.mkdir(parents=True)
    transcript = projects / "abc-123.jsonl"
    transcript.write_text('{"role":"assistant","content":"hi"}\n')

    monkeypatch.setattr(tg.Path, "home", lambda: tmp_path)

    result = tg._find_transcript("abc-123")
    assert result is not None
    assert result.name == "abc-123.jsonl"


# ── command menu ─────────────────────────────────────────────────────────────


def test_register_commands(tg):
    tg.register_commands()
    methods = [m for m, _ in tg._test_api_calls]
    assert "setMyCommands" in methods
