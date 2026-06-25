"""Tests for usage tracking and the shipped-feature 'used-or-failure' verdict."""

import sqlite3

import pytest

import agent_interface.features as features
from agent_interface.usage import record_usage, usage_count, usage_summary


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "MANIFEST_PATH", tmp_path / "features.json")
    return tmp_path / "features.json"


# ── usage ledger ──────────────────────────────────────────────────────────────


def test_record_and_count(conn):
    record_usage("feat-x", conn=conn, now=100.0)
    record_usage("feat-x", conn=conn, now=200.0)
    record_usage("feat-y", conn=conn, now=150.0)
    assert usage_count(conn, "feat-x") == 2
    assert usage_count(conn, "feat-y") == 1
    assert usage_count(conn, "feat-z") == 0


def test_count_since(conn):
    record_usage("feat-x", conn=conn, now=100.0)
    record_usage("feat-x", conn=conn, now=300.0)
    assert usage_count(conn, "feat-x", since=200.0) == 1


def test_usage_summary(conn):
    record_usage("a", conn=conn, now=1.0)
    record_usage("a", conn=conn, now=2.0)
    record_usage("b", conn=conn, now=3.0)
    assert usage_summary(conn) == {"a": 2, "b": 1}


def test_record_usage_never_raises():
    # No conn, bad DB path — must swallow, not raise.
    record_usage("feat-x", conn="not-a-conn")  # type: ignore[arg-type]


# ── feature registry / verdict ────────────────────────────────────────────────


def test_register_is_idempotent(manifest):
    features.register("feat-1", "Title", now=0.0)
    features.register("feat-1", "Title again", now=0.0)
    assert len(features.list_features()) == 1


def test_pending_within_grace_not_judged(manifest, conn):
    features.register("feat-1", "T", now=0.0, grace_seconds=1000)
    verdict = features.evaluate(conn, now=500.0)  # still within grace
    assert verdict == {"used": [], "failed": []}
    assert features.list_features()[0]["status"] == "shipped"


def test_used_when_usage_after_grace(manifest, conn):
    features.register("feat-1", "T", now=0.0, grace_seconds=100)
    record_usage("feat-1", conn=conn, now=50.0)  # used during grace
    verdict = features.evaluate(conn, now=200.0)
    assert [f["id"] for f in verdict["used"]] == ["feat-1"]
    assert features.list_features()[0]["status"] == "used"


def test_failed_when_unused_after_grace(manifest, conn):
    features.register("feat-1", "T", now=0.0, grace_seconds=100)
    verdict = features.evaluate(conn, now=200.0)  # grace elapsed, zero uses
    assert [f["id"] for f in verdict["failed"]] == ["feat-1"]
    assert features.list_features()[0]["status"] == "failed"


def test_verdict_is_sticky(manifest, conn):
    """Once judged, a feature isn't re-evaluated (status no longer 'shipped')."""
    features.register("feat-1", "T", now=0.0, grace_seconds=100)
    features.evaluate(conn, now=200.0)  # → failed
    record_usage("feat-1", conn=conn, now=250.0)  # late use
    verdict = features.evaluate(conn, now=300.0)
    assert verdict == {"used": [], "failed": []}  # not re-judged
    assert features.list_features()[0]["status"] == "failed"
