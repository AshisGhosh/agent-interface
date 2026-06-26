"""Tests for durable background processes (`agi up` / `agi down`)."""

import os
import time

import pytest

import agent_interface.daemon as daemon


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "reg.db"))
    monkeypatch.setattr(daemon, "LOG_DIR", tmp_path / "daemons")
    # Treat cwd as its own project (no git lookup surprises).
    monkeypatch.setattr(daemon, "_repo_key", lambda cwd: "/proj")
    return tmp_path


def test_launch_is_detached_and_logged(env):
    info = daemon.launch(["sh", "-c", "echo hello; sleep 5"], name="t", cwd="/tmp")
    assert info["name"] == "t"
    assert info["status"] == "running"
    # New session leader → detached from the test's process group.
    assert os.getpgid(info["pid"]) == info["pid"]
    time.sleep(0.3)
    assert "hello" in open(info["log_path"]).read()
    daemon.stop("t", cwd="/tmp")


def test_launch_records_in_ledger(env):
    daemon.launch(["sleep", "5"], name="s", cwd="/tmp")
    rows = daemon.list_daemons(cwd="/tmp")
    assert [r["name"] for r in rows] == ["s"]
    assert rows[0]["status"] == "running"
    daemon.stop("s", cwd="/tmp")


def test_duplicate_running_name_rejected(env):
    daemon.launch(["sleep", "5"], name="dup", cwd="/tmp")
    with pytest.raises(ValueError, match="already running"):
        daemon.launch(["sleep", "5"], name="dup", cwd="/tmp")
    daemon.stop("dup", cwd="/tmp")


def test_stop_kills_and_marks(env):
    info = daemon.launch(["sleep", "30"], name="k", cwd="/tmp")
    assert daemon.stop("k", cwd="/tmp") is True
    time.sleep(0.3)
    assert not daemon._alive(info["pid"])
    rows = daemon.list_daemons(cwd="/tmp")
    assert rows[0]["status"] == "stopped"


def test_list_marks_exited_when_process_gone(env):
    info = daemon.launch(["sh", "-c", "exit 0"], name="quick", cwd="/tmp")
    time.sleep(0.3)
    assert not daemon._alive(info["pid"])
    rows = daemon.list_daemons(cwd="/tmp")
    assert rows[0]["status"] == "exited"


def test_stop_unknown_returns_false(env):
    assert daemon.stop("nope", cwd="/tmp") is False


def test_relaunch_after_exit_allowed(env):
    daemon.launch(["sh", "-c", "exit 0"], name="re", cwd="/tmp")
    time.sleep(0.3)
    daemon.list_daemons(cwd="/tmp")  # marks exited
    # Same name can be reused once the old one is gone.
    info = daemon.launch(["sleep", "5"], name="re", cwd="/tmp")
    assert info["status"] == "running"
    daemon.stop("re", cwd="/tmp")
