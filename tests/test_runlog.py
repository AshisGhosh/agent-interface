"""Tests for the per-project command runbook (agi run / agi runs)."""

import sqlite3

import pytest

from agent_interface.runlog import (
    build_command,
    last_run,
    list_runs,
    project_key,
    record_run,
    run_command,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── journal ───────────────────────────────────────────────────────────────────


def test_record_and_list(conn):
    record_run(
        conn, project="/p", cmd="echo a", cwd="/p", exit_code=0,
        duration_s=1.0, started_at=100.0,
    )
    record_run(
        conn, project="/p", cmd="echo b", cwd="/p", exit_code=1,
        duration_s=2.0, started_at=200.0,
    )
    runs = list_runs(conn, "/p")
    assert [r["cmd"] for r in runs] == ["echo b", "echo a"]  # newest first
    assert runs[0]["exit_code"] == 1


def test_runs_are_project_scoped(conn):
    record_run(conn, project="/a", cmd="x", cwd="/a", exit_code=0, duration_s=0.1)
    record_run(conn, project="/b", cmd="y", cwd="/b", exit_code=0, duration_s=0.1)
    assert [r["cmd"] for r in list_runs(conn, "/a")] == ["x"]
    assert [r["cmd"] for r in list_runs(conn, "/b")] == ["y"]


def test_name_filter_and_last(conn):
    record_run(
        conn, project="/p", cmd="run eval", cwd="/p", exit_code=0,
        duration_s=1.0, name="eval", started_at=100.0,
    )
    record_run(
        conn, project="/p", cmd="run nav", cwd="/p", exit_code=0,
        duration_s=1.0, name="nav", started_at=200.0,
    )
    record_run(
        conn, project="/p", cmd="run eval v2", cwd="/p", exit_code=0,
        duration_s=1.0, name="eval", started_at=300.0,
    )
    assert [r["cmd"] for r in list_runs(conn, "/p", name="eval")] == [
        "run eval v2", "run eval",
    ]
    assert last_run(conn, "/p", name="eval")["cmd"] == "run eval v2"
    assert last_run(conn, "/p")["cmd"] == "run eval v2"
    assert last_run(conn, "/p", name="missing") is None


def test_limit(conn):
    for i in range(5):
        record_run(
            conn, project="/p", cmd=f"c{i}", cwd="/p", exit_code=0,
            duration_s=0.1, started_at=float(i),
        )
    assert len(list_runs(conn, "/p", limit=3)) == 3


# ── command reconstruction ──────────────────────────────────────────────────────


def test_build_command_single_arg_verbatim():
    # A single quoted arg keeps shell syntax intact.
    assert build_command(["a | b && c"]) == "a | b && c"


def test_build_command_multi_arg_quoted():
    assert build_command(["python", "eval.py", "--scene", "a b"]) == (
        "python eval.py --scene 'a b'"
    )


# ── execution ───────────────────────────────────────────────────────────────────


def test_run_command_captures(tmp_path):
    code, dur, tail = run_command("echo hello", str(tmp_path))
    assert code == 0
    assert "hello" in tail
    assert dur >= 0


def test_run_command_nonzero_exit(tmp_path):
    code, _dur, _tail = run_command("exit 3", str(tmp_path))
    assert code == 3


def test_run_command_tail_bounded(tmp_path):
    code, _dur, tail = run_command(
        "for i in $(seq 1 100); do echo line$i; done", str(tmp_path), tail_lines=5,
    )
    assert code == 0
    lines = tail.strip().splitlines()
    assert len(lines) == 5
    assert lines[-1] == "line100"


def test_run_command_streams(tmp_path):
    seen = []
    run_command("echo one; echo two", str(tmp_path), stream=seen.append)
    assert "".join(seen).count("\n") == 2


# ── project key ─────────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    # tmp_path is not a git repo → key is the resolved path.
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
