"""Tests for the per-project flaky-test ledger (agi flake / agi flakes)."""

import sqlite3

import pytest

from agent_interface.flake import (
    classify,
    clear_results,
    flaky_stats,
    history,
    normalize_status,
    project_key,
    record_result,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── status normalisation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("pass", "pass"),
        ("PASSED", "pass"),
        ("ok", "pass"),
        ("green", "pass"),
        ("fail", "fail"),
        ("Failed", "fail"),
        ("error", "fail"),
        ("RED", "fail"),
    ],
)
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


def test_normalize_status_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_status("maybe")


# ── classify ──────────────────────────────────────────────────────────────────


def test_classify():
    assert classify(3, 2) == "flaky"
    assert classify(0, 4) == "failing"
    assert classify(5, 0) == "passing"
    assert classify(0, 0) == "unknown"


# ── record / stats ─────────────────────────────────────────────────────────────


def test_record_normalizes_status(conn):
    record_result(conn, project="/p", test="t", status="failed", ran_at=1.0)
    rows = history(conn, "/p", "t")
    assert rows[0]["status"] == "fail"


def test_stats_aggregates_and_classifies(conn):
    # a flaky test: passes and fails
    record_result(conn, project="/p", test="block-rel-test", status="fail", ran_at=1.0)
    record_result(conn, project="/p", test="block-rel-test", status="pass", ran_at=2.0)
    record_result(conn, project="/p", test="block-rel-test", status="fail", ran_at=3.0)
    # a deterministic failure
    record_result(conn, project="/p", test="loop-back", status="fail", ran_at=4.0)
    # a stable pass
    record_result(conn, project="/p", test="sim_fast", status="pass", ran_at=5.0)

    stats = {s["test"]: s for s in flaky_stats(conn, "/p")}

    brt = stats["block-rel-test"]
    assert brt["kind"] == "flaky"
    assert (brt["passes"], brt["fails"], brt["total"]) == (1, 2, 3)
    assert brt["fail_rate"] == pytest.approx(2 / 3)
    assert brt["last_status"] == "fail"  # most recent outcome
    assert brt["last_seen"] == 3.0

    assert stats["loop-back"]["kind"] == "failing"
    assert stats["sim_fast"]["kind"] == "passing"


def test_stats_orders_flaky_first(conn):
    record_result(conn, project="/p", test="stable", status="pass", ran_at=1.0)
    record_result(conn, project="/p", test="flaky", status="pass", ran_at=2.0)
    record_result(conn, project="/p", test="flaky", status="fail", ran_at=3.0)
    record_result(conn, project="/p", test="broken", status="fail", ran_at=4.0)

    order = [s["test"] for s in flaky_stats(conn, "/p")]
    assert order[0] == "flaky"  # flaky always sorts first


def test_stats_flaky_only_filter(conn):
    record_result(conn, project="/p", test="flaky", status="pass", ran_at=1.0)
    record_result(conn, project="/p", test="flaky", status="fail", ran_at=2.0)
    record_result(conn, project="/p", test="broken", status="fail", ran_at=3.0)

    names = [s["test"] for s in flaky_stats(conn, "/p", flaky_only=True)]
    assert names == ["flaky"]


def test_stats_name_filter_case_insensitive(conn):
    record_result(conn, project="/p", test="MotorFirmware", status="fail", ran_at=1.0)
    record_result(conn, project="/p", test="nav-eval", status="pass", ran_at=2.0)
    names = [s["test"] for s in flaky_stats(conn, "/p", name="motor")]
    assert names == ["MotorFirmware"]


def test_stats_are_project_scoped(conn):
    record_result(conn, project="/a", test="alpha", status="fail", ran_at=1.0)
    record_result(conn, project="/b", test="beta", status="pass", ran_at=1.0)
    assert [s["test"] for s in flaky_stats(conn, "/a")] == ["alpha"]
    assert [s["test"] for s in flaky_stats(conn, "/b")] == ["beta"]


# ── history / clear ─────────────────────────────────────────────────────────────


def test_history_most_recent_first(conn):
    record_result(conn, project="/p", test="t", status="fail", ran_at=1.0)
    record_result(conn, project="/p", test="t", status="pass", ran_at=2.0)
    rows = history(conn, "/p", "t")
    assert [r["status"] for r in rows] == ["pass", "fail"]


def test_clear_single_test_is_scoped(conn):
    record_result(conn, project="/p", test="t1", status="fail", ran_at=1.0)
    record_result(conn, project="/p", test="t2", status="fail", ran_at=2.0)
    assert clear_results(conn, "/p", test="t1") == 1
    assert [s["test"] for s in flaky_stats(conn, "/p")] == ["t2"]


def test_clear_all_for_project(conn):
    record_result(conn, project="/p", test="t1", status="fail", ran_at=1.0)
    record_result(conn, project="/p", test="t2", status="pass", ran_at=2.0)
    record_result(conn, project="/q", test="keep", status="pass", ran_at=3.0)
    assert clear_results(conn, "/p") == 2
    assert flaky_stats(conn, "/p") == []
    assert [s["test"] for s in flaky_stats(conn, "/q")] == ["keep"]


# ── project key ─────────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
