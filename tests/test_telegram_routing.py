"""Tests for resolving a session by name through Telegram.

Covers the two ambiguity failure modes:
  - reply routing must never resolve to a dead (done/archived) session whose
    tmux pane is gone, and
  - when several live sessions match, the disambiguation list must be
    actionable (show the pid, which is itself a unique query).
"""

import json
import os

import pytest

from agent_interface.db import get_connection
from agent_interface.models import Session
from agent_interface.registry import register_session


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "test.db"))
    return get_connection()


@pytest.fixture
def tg(tmp_path, monkeypatch):
    import agent_interface.telegram as tg_mod

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "telegram_bot_token": "fake-token",
        "telegram_chat_id": 12345,
    }))
    monkeypatch.setattr(tg_mod, "CONFIG_PATH", config_path)

    sent: list[str] = []

    def fake_api(token, method, data, timeout=15):
        if method == "sendMessage":
            sent.append(data["text"])
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(tg_mod, "_api", fake_api)
    tg_mod._sent = sent
    return tg_mod


def _session(id, label, pid, state="running"):
    return Session(
        id=id, label=label, pid=pid, state=state, cwd="/tmp/proj",
        tmux_session="0", tmux_window="0", tmux_pane="0",
    )


# ── never route to a dead session ─────────────────────────────────────────────


def test_at_reply_skips_done_session(tg, conn):
    """A `done` session must not be a reply target even on an exact label hit."""
    register_session(conn, _session("s1", "deploy", os.getpid(), state="done"))

    tg._handle_at_reply("@deploy restart it")

    assert any("No active session" in m for m in tg._sent)
    # And we did NOT try to confirm a send.
    assert not any("→" in m for m in tg._sent)


def test_at_reply_routes_single_active(tg, conn, monkeypatch):
    register_session(conn, _session("s1", "solo-task", os.getpid()))
    monkeypatch.setattr(tg, "_resolve_tmux_target", lambda pid: "0:0.0")
    monkeypatch.setattr(tg, "send_to_tmux", lambda t, m: (True, ""))

    tg._handle_at_reply("@solo go ahead")

    assert any("solo-task" in m for m in tg._sent)


# ── actionable ambiguity ──────────────────────────────────────────────────────


def test_at_reply_ambiguous_lists_pids(tg, conn):
    """Two live sessions share a label; the prompt must show pids to pick from."""
    p1, p2 = os.getpid(), os.getppid()
    register_session(conn, _session("s1", "api", p1))
    register_session(conn, _session("s2", "api", p2))

    tg._handle_at_reply("@api ship it")

    msg = next(m for m in tg._sent if "Ambiguous" in m)
    assert str(p1) in msg
    assert str(p2) in msg
    # Tells the user how to disambiguate.
    assert "@" in msg and "pid" in msg.lower()


def test_ambiguous_pid_query_resolves_uniquely(tg, conn, monkeypatch):
    """Replying with the pid (shown in the ambiguity list) routes to exactly one."""
    p1, p2 = os.getpid(), os.getppid()
    register_session(conn, _session("s1", "api", p1))
    register_session(conn, _session("s2", "api", p2))
    monkeypatch.setattr(tg, "_resolve_tmux_target", lambda pid: "0:0.0")
    monkeypatch.setattr(tg, "send_to_tmux", lambda t, m: (True, ""))

    tg._handle_at_reply(f"@{p1} do the thing")

    # Routed (a confirmation with the arrow), not ambiguous.
    assert not any("Ambiguous" in m for m in tg._sent)
    assert any("→" in m for m in tg._sent)


def test_peek_ambiguous_uses_pid_format(tg, conn):
    register_session(conn, _session("s1", "api", os.getpid()))
    register_session(conn, _session("s2", "api", os.getppid()))

    tg._handle_command("fake-token", 12345, "/peek api")

    assert any("Ambiguous" in m and str(os.getpid()) in m for m in tg._sent)
