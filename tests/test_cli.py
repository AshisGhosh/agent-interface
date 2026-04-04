"""Tests for the CLI commands."""

import os

import pytest
from typer.testing import CliRunner

# Point DB at a temp location before importing the app.
os.environ["AGI_DB_PATH"] = ":memory:"

from agent_interface.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Give each test a fresh database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("AGI_DB_PATH", db_path)


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "agi" in result.output.lower() or "list" in result.output.lower()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "agi" in result.output


def test_bare_agi_defaults_to_list():
    runner.invoke(app, ["register", "--id", "s1", "--host", "h"])
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "s1" in result.output


def test_register_and_list():
    result = runner.invoke(app, [
        "register",
        "--host", "myhost",
        "--cwd", "/tmp/work",
        "--id", "sess-1",
    ])
    assert result.exit_code == 0
    assert "sess-1" in result.output

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "sess-1" in result.output
    assert "/tmp/work" in result.output


def test_register_auto_id():
    result = runner.invoke(app, ["register", "--host", "h"])
    assert result.exit_code == 0
    assert "Registered session:" in result.output


def test_show():
    runner.invoke(app, ["register", "--id", "s1", "--host", "h"])
    result = runner.invoke(app, ["show", "s1"])
    assert result.exit_code == 0
    assert "s1" in result.output


def test_show_unknown():
    result = runner.invoke(app, ["show", "nope"])
    assert result.exit_code == 1


def test_rename():
    runner.invoke(app, ["register", "--id", "s1"])
    result = runner.invoke(app, ["rename", "s1", "my-label"])
    assert result.exit_code == 0
    assert "my-label" in result.output

    result = runner.invoke(app, ["show", "s1"])
    assert "my-label" in result.output


def test_archive_and_restore():
    runner.invoke(app, ["register", "--id", "s1"])

    result = runner.invoke(app, ["archive", "s1"])
    assert result.exit_code == 0

    # Archived session should not appear in default list.
    result = runner.invoke(app, ["list"])
    assert "s1" not in result.output

    # But should appear with --all.
    result = runner.invoke(app, ["list", "--all"])
    assert "s1" in result.output

    # Restore it.
    result = runner.invoke(app, ["restore", "s1"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["list"])
    assert "s1" in result.output


def test_update_state():
    runner.invoke(app, ["register", "--id", "s1"])
    result = runner.invoke(app, ["update-state", "s1", "waiting_for_user"])
    assert result.exit_code == 0
    assert "waiting_for_user" in result.output


def test_update_state_invalid():
    runner.invoke(app, ["register", "--id", "s1"])
    result = runner.invoke(app, ["update-state", "s1", "bogus"])
    assert result.exit_code == 1
    assert "Invalid state" in result.output


def test_waiting_filter():
    runner.invoke(app, ["register", "--id", "s1", "--state", "running"])
    runner.invoke(app, ["register", "--id", "s2", "--state", "waiting_for_user"])

    result = runner.invoke(app, ["waiting"])
    assert result.exit_code == 0
    assert "s2" in result.output
    assert "s1" not in result.output


def test_done_hidden_from_list():
    runner.invoke(app, ["register", "--id", "s1"])
    runner.invoke(app, ["update-state", "s1", "done"])

    result = runner.invoke(app, ["list"])
    assert "s1" not in result.output

    result = runner.invoke(app, ["list", "--all"])
    assert "s1" in result.output


def test_register_with_pid():
    result = runner.invoke(app, [
        "register",
        "--id", "s1",
        "--pid", "99999",
    ])
    assert result.exit_code == 0

    result = runner.invoke(app, ["show", "s1"])
    assert "99999" in result.output
