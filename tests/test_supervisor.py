"""Tests for systemd unit rendering (the 'never goes down' contract)."""

from agent_interface import supervisor
from agent_interface.supervisor import (
    BOT_SERVICE,
    HEARTBEAT_SERVICE,
    HEARTBEAT_TIMER,
    render_units,
)


def test_renders_all_three_units():
    units = render_units(agi="/usr/bin/agi")
    assert set(units) == {BOT_SERVICE, HEARTBEAT_SERVICE, HEARTBEAT_TIMER}


def test_bot_service_restarts_always():
    units = render_units(agi="/usr/bin/agi")
    bot = units[BOT_SERVICE]
    # The core of 'never goes down by design'.
    assert "Restart=always" in bot
    assert "ExecStart=/usr/bin/agi bot --fg" in bot
    assert "WantedBy=default.target" in bot


def test_heartbeat_service_runs_heartbeat():
    units = render_units(agi="/opt/agi")
    svc = units[HEARTBEAT_SERVICE]
    assert "ExecStart=/opt/agi heartbeat" in svc
    assert "Type=oneshot" in svc


def test_services_set_path_for_dispatch_tools():
    """Bot + heartbeat must find claude/tmux/git via ~/.local/bin."""
    units = render_units(agi="/usr/bin/agi")
    for unit in (BOT_SERVICE, HEARTBEAT_SERVICE):
        assert "%h/.local/bin" in units[unit]


def test_heartbeat_timer_is_periodic_and_persistent():
    units = render_units(agi="/usr/bin/agi")
    timer = units[HEARTBEAT_TIMER]
    assert f"OnUnitActiveSec={supervisor.HEARTBEAT_INTERVAL_SEC}s" in timer
    assert "OnBootSec=30s" in timer
    assert "Persistent=true" in timer  # catch up after downtime
    assert "WantedBy=timers.target" in timer


def test_uses_resolved_agi_path_by_default(monkeypatch):
    monkeypatch.setattr(supervisor, "_agi_path", lambda: "/resolved/agi")
    units = render_units()
    assert "ExecStart=/resolved/agi bot --fg" in units[BOT_SERVICE]
