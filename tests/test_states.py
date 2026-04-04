"""Tests for session states and stale detection."""

from datetime import datetime, timedelta, timezone

from agent_interface.models import Session
from agent_interface.registry import is_stale
from agent_interface.states import ACTIVE_STATES, STALE_THRESHOLD_SECONDS, SessionState


def test_all_states_are_strings():
    for s in SessionState:
        assert isinstance(s.value, str)


def test_active_states_do_not_include_done_or_archived():
    assert SessionState.DONE not in ACTIVE_STATES
    assert SessionState.ARCHIVED not in ACTIVE_STATES
    assert SessionState.STALE not in ACTIVE_STATES


def test_waiting_is_active():
    assert SessionState.WAITING_FOR_USER in ACTIVE_STATES


def test_stale_detection_fresh():
    s = Session(id="fresh", state="running")
    # Default last_seen_at is now, so not stale.
    assert not is_stale(s)


def test_stale_detection_old():
    old = (datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60))
    s = Session(
        id="old",
        state="running",
        last_seen_at=old.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert is_stale(s)


def test_stale_not_applied_to_done():
    old = (datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60))
    s = Session(
        id="old-done",
        state="done",
        last_seen_at=old.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert not is_stale(s)


def test_stale_not_applied_to_archived():
    old = (datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS + 60))
    s = Session(
        id="old-archived",
        state="archived",
        last_seen_at=old.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert not is_stale(s)
