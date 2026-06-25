"""Tests for the autonomous optimizer's guardrails and decision logic.

These are the spend-safety checks: nothing should dispatch unless explicitly
enabled, the kill-switch must always win, and the daily-cap / cooldown must
hold across day rollovers.
"""

import agent_interface.optimizer as opt
from agent_interface.insights import WorkflowOpportunity
from agent_interface.optimizer import (
    build_task_spec,
    roll_over_day,
    select_target,
    should_dispatch,
)

CFG = {
    "enabled": True,
    "max_dispatches_per_day": 2,
    "min_interval_seconds": 3600,
}
DAY0 = 1_000_000.0  # arbitrary fixed epoch


def _state(**kw):
    base = {"day": "", "dispatches_today": 0, "last_run": 0.0, "acted_repos": []}
    base.update(kw)
    return base


# ── kill-switch + enabled flag ────────────────────────────────────────────────


def test_killswitch_always_blocks():
    d = should_dispatch(_state(), CFG, DAY0, killswitch=True)
    assert not d.ok and "kill-switch" in d.reason


def test_disabled_blocks():
    d = should_dispatch(_state(), {**CFG, "enabled": False}, DAY0, killswitch=False)
    assert not d.ok and "disabled" in d.reason


def test_killswitch_beats_enabled():
    """Even fully enabled and otherwise eligible, the kill-switch wins."""
    d = should_dispatch(_state(), CFG, DAY0, killswitch=True)
    assert not d.ok


# ── daily cap ─────────────────────────────────────────────────────────────────


def test_daily_cap_blocks_when_reached():
    day = opt._day_str(DAY0)
    st = _state(day=day, dispatches_today=2, last_run=0.0)
    d = should_dispatch(st, CFG, DAY0, killswitch=False)
    assert not d.ok and "daily cap" in d.reason


def test_cap_resets_next_day():
    day = opt._day_str(DAY0)
    st = _state(day=day, dispatches_today=2, last_run=0.0)
    # ~26h later → new UTC day → counter rolls over → cap no longer blocks.
    later = DAY0 + 26 * 3600
    d = should_dispatch(st, CFG, later, killswitch=False)
    assert d.ok, d.reason


def test_roll_over_day_resets_counter():
    st = _state(day="1999-01-01", dispatches_today=5)
    rolled = roll_over_day(st, DAY0)
    assert rolled["dispatches_today"] == 0
    assert rolled["day"] == opt._day_str(DAY0)


# ── cooldown ──────────────────────────────────────────────────────────────────


def test_cooldown_blocks_within_interval():
    day = opt._day_str(DAY0)
    st = _state(day=day, dispatches_today=0, last_run=DAY0 - 100)
    d = should_dispatch(st, CFG, DAY0, killswitch=False)
    assert not d.ok and "cooldown" in d.reason


def test_passes_after_cooldown():
    day = opt._day_str(DAY0)
    st = _state(day=day, dispatches_today=0, last_run=DAY0 - 4000)
    d = should_dispatch(st, CFG, DAY0, killswitch=False)
    assert d.ok


# ── target selection / dedupe ─────────────────────────────────────────────────


def _opp(repo, n):
    return WorkflowOpportunity(repo=repo, session_count=n, keywords=[("x", n)])


def test_select_highest_unacted():
    opps = [_opp("/a", 5), _opp("/b", 4)]
    assert select_target(opps, acted_repos=[]).repo == "/a"


def test_select_skips_acted():
    opps = [_opp("/a", 5), _opp("/b", 4)]
    assert select_target(opps, acted_repos=["/a"]).repo == "/b"


def test_select_none_when_all_acted():
    opps = [_opp("/a", 5), _opp("/b", 4)]
    assert select_target(opps, acted_repos=["/a", "/b"]) is None


# ── task spec ─────────────────────────────────────────────────────────────────


def test_build_task_spec_is_scoped_and_defensive():
    opp = WorkflowOpportunity(
        repo="/home/u/proj", session_count=6,
        keywords=[("deploy", 5), ("ci", 3)],
        sample_labels=["deploy to staging", "fix ci"],
    )
    title, desc = build_task_spec(opp)
    assert "proj" in title
    assert len(title) <= 80
    assert "do NOT delete data" in desc
    assert "docs/workflows/" in desc


# ── maybe_run never raises ────────────────────────────────────────────────────


def test_maybe_run_blocked_does_not_dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(opt, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(opt, "KILLSWITCH_PATH", tmp_path / "off")
    monkeypatch.setattr(opt, "_config", lambda: {**opt.DEFAULTS, "enabled": False})

    result = opt.maybe_run(now=DAY0)
    assert result["dispatched"] is False
    assert "disabled" in result["reason"]


def test_maybe_run_swallows_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(opt, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(opt, "AUDIT_PATH", tmp_path / "audit.log")
    monkeypatch.setattr(opt, "KILLSWITCH_PATH", tmp_path / "off")

    def boom():
        raise RuntimeError("config blew up")

    monkeypatch.setattr(opt, "_config", boom)

    result = opt.maybe_run(now=DAY0)
    assert result["dispatched"] is False
    assert "error" in result["reason"]
