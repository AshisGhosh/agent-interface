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


def test_help_short():
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "agi" in result.output


def test_bare_agi_defaults_to_list():
    runner.invoke(app, ["register", "--id", "s1", "--cwd", "/tmp/myproject"])
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "myproject" in result.output


def test_register_and_list():
    result = runner.invoke(app, [
        "register",
        "--host", "myhost",
        "--cwd", "/tmp/work",
        "--id", "sess-1",
        "--label", "test task",
    ])
    assert result.exit_code == 0
    assert "sess-1" in result.output

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "test task" in result.output
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
    runner.invoke(app, ["register", "--id", "s1", "--label", "trackme"])

    result = runner.invoke(app, ["archive", "s1"])
    assert result.exit_code == 0

    # Archived session should not appear in default list.
    result = runner.invoke(app, ["list"])
    assert "trackme" not in result.output

    # But should appear with --all.
    result = runner.invoke(app, ["list", "--all"])
    assert "trackme" in result.output

    # Restore it.
    result = runner.invoke(app, ["restore", "s1"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["list"])
    assert "trackme" in result.output


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
    runner.invoke(app, [
        "register", "--id", "s1", "--state", "running", "--label", "task-a",
    ])
    runner.invoke(app, [
        "register", "--id", "s2", "--state", "waiting_for_user", "--label", "task-b",
    ])

    result = runner.invoke(app, ["waiting"])
    assert result.exit_code == 0
    assert "task-b" in result.output
    assert "task-a" not in result.output


def test_done_hidden_from_list():
    runner.invoke(app, ["register", "--id", "s1", "--label", "trackme"])
    runner.invoke(app, ["update-state", "s1", "done"])

    result = runner.invoke(app, ["list"])
    assert "trackme" not in result.output

    result = runner.invoke(app, ["list", "--all"])
    assert "trackme" in result.output


def test_register_with_pid():
    result = runner.invoke(app, [
        "register",
        "--id", "s1",
        "--pid", "99999",
    ])
    assert result.exit_code == 0

    result = runner.invoke(app, ["show", "s1"])
    assert "99999" in result.output


# ── run / runs (command runbook) ───────────────────────────────────────────────


def test_run_records_and_lists():
    result = runner.invoke(app, ["run", "echo", "hello-runbook"])
    assert result.exit_code == 0
    assert "hello-runbook" in result.output

    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0
    assert "echo hello-runbook" in result.output


def test_run_propagates_exit_code():
    result = runner.invoke(app, ["run", "exit 7"])
    assert result.exit_code == 7


def test_run_replay_by_name():
    runner.invoke(app, ["run", "--name", "eval", "echo", "first-eval"])
    result = runner.invoke(app, ["run", "--replay", "eval"])
    assert result.exit_code == 0
    assert "replaying" in result.output
    assert "echo first-eval" in result.output


def test_run_last():
    runner.invoke(app, ["run", "echo", "the-last-one"])
    result = runner.invoke(app, ["run", "--last"])
    assert result.exit_code == 0
    assert "echo the-last-one" in result.output


def test_run_replay_missing_name():
    result = runner.invoke(app, ["run", "--replay", "nope"])
    assert result.exit_code == 1
    assert "No prior run" in result.output


def test_run_no_command():
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "No command given" in result.output


def test_runs_empty():
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0
    assert "No runs recorded" in result.output


def test_runs_name_filter():
    runner.invoke(app, ["run", "--name", "nav", "echo", "nav-run"])
    runner.invoke(app, ["run", "--name", "eval", "echo", "eval-run"])
    result = runner.invoke(app, ["runs", "--name", "nav"])
    assert result.exit_code == 0
    assert "nav-run" in result.output
    assert "eval-run" not in result.output


# ── note / notes (project notebook) ─────────────────────────────────────────────


def test_note_records_and_lists():
    result = runner.invoke(app, ["note", "build", "needs", "node", "18"])
    assert result.exit_code == 0
    assert "noted" in result.output

    result = runner.invoke(app, ["notes"])
    assert result.exit_code == 0
    assert "build needs node 18" in result.output


def test_note_requires_text():
    result = runner.invoke(app, ["note"])
    assert result.exit_code == 1
    assert "No note given" in result.output


def test_notes_empty():
    result = runner.invoke(app, ["notes"])
    assert result.exit_code == 0
    assert "No notes" in result.output


def test_notes_tag_filter():
    runner.invoke(app, ["note", "--tag", "ci", "retry", "flaky", "test"])
    runner.invoke(app, ["note", "general", "hint"])
    result = runner.invoke(app, ["notes", "--tag", "ci"])
    assert result.exit_code == 0
    assert "retry flaky test" in result.output
    assert "general hint" not in result.output


def test_notes_search_filter():
    runner.invoke(app, ["note", "the deploy command is fly deploy"])
    runner.invoke(app, ["note", "unrelated thing"])
    result = runner.invoke(app, ["notes", "--search", "deploy"])
    assert result.exit_code == 0
    assert "fly deploy" in result.output
    assert "unrelated thing" not in result.output


def test_notes_remove():
    runner.invoke(app, ["note", "ephemeral"])
    listed = runner.invoke(app, ["notes"])
    assert "ephemeral" in listed.output
    # first (only) note is id #1
    result = runner.invoke(app, ["notes", "--rm", "1"])
    assert result.exit_code == 0
    assert "removed note #1" in result.output
    assert "ephemeral" not in runner.invoke(app, ["notes"]).output


def test_notes_remove_missing():
    result = runner.invoke(app, ["notes", "--rm", "999"])
    assert result.exit_code == 1
    assert "No note #999" in result.output


# ── jobs (cluster/remote job ledger) ─────────────────────────────────────────


def test_job_records_and_lists():
    result = runner.invoke(
        app, ["job", "H100 sweep", "--id", "481923", "--aim", "https://aim/run/ab12"]
    )
    assert result.exit_code == 0
    assert "tracked job #1" in result.output

    result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0
    assert "H100 sweep" in result.output
    assert "481923" in result.output


def test_job_requires_title():
    result = runner.invoke(app, ["job"])
    assert result.exit_code == 1
    assert "No job title" in result.output


def test_job_rejects_bad_status():
    result = runner.invoke(app, ["job", "x", "--status", "bogus"])
    assert result.exit_code == 1
    assert "Invalid status" in result.output


def test_job_update_bumps_status():
    runner.invoke(app, ["job", "sweep", "--id", "1"])
    result = runner.invoke(app, ["job", "--update", "1", "--status", "running"])
    assert result.exit_code == 0
    assert "running" in result.output
    # status reflected in the listing
    assert "running" in runner.invoke(app, ["jobs"]).output


def test_job_update_missing():
    result = runner.invoke(app, ["job", "--update", "999", "--status", "done"])
    assert result.exit_code == 1
    assert "No job #999" in result.output


def test_jobs_open_filter():
    runner.invoke(app, ["job", "live", "--status", "running"])
    runner.invoke(app, ["job", "finished", "--status", "done"])
    result = runner.invoke(app, ["jobs", "--open"])
    assert result.exit_code == 0
    assert "live" in result.output
    assert "finished" not in result.output


def test_jobs_empty():
    result = runner.invoke(app, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs tracked" in result.output


def test_jobs_remove():
    runner.invoke(app, ["job", "ephemeral-job"])
    result = runner.invoke(app, ["jobs", "--rm", "1"])
    assert result.exit_code == 0
    assert "removed job #1" in result.output
    assert "ephemeral-job" not in runner.invoke(app, ["jobs"]).output


# ── flake / flakes (flaky-test ledger) ──────────────────────────────────────────


def test_flake_records_and_reports():
    result = runner.invoke(app, ["flake", "block-rel-test", "-s", "fail"])
    assert result.exit_code == 0
    assert "recorded" in result.output

    result = runner.invoke(app, ["flakes"])
    assert result.exit_code == 0
    assert "block-rel-test" in result.output


def test_flake_defaults_to_fail():
    runner.invoke(app, ["flake", "motor-firmware"])
    out = runner.invoke(app, ["flakes"]).output
    assert "motor-firmware" in out
    assert "failing" in out


def test_flake_detects_flaky():
    runner.invoke(app, ["flake", "loop-back", "-s", "fail"])
    runner.invoke(app, ["flake", "loop-back", "-s", "pass"])
    out = runner.invoke(app, ["flakes"]).output
    assert "loop-back" in out
    assert "flaky" in out


def test_flake_status_synonyms():
    result = runner.invoke(app, ["flake", "sim-eval", "-s", "passed"])
    assert result.exit_code == 0
    assert "pass" in result.output


def test_flake_rejects_bad_status():
    result = runner.invoke(app, ["flake", "t", "-s", "maybe"])
    assert result.exit_code == 1
    assert "unknown status" in result.output


def test_flake_requires_test_name():
    result = runner.invoke(app, ["flake"])
    assert result.exit_code == 1
    assert "No test name given" in result.output


def test_flakes_empty():
    result = runner.invoke(app, ["flakes"])
    assert result.exit_code == 0
    assert "No test results" in result.output


def test_flakes_flaky_only_filter():
    runner.invoke(app, ["flake", "flaky-one", "-s", "pass"])
    runner.invoke(app, ["flake", "flaky-one", "-s", "fail"])
    runner.invoke(app, ["flake", "always-broken", "-s", "fail"])
    out = runner.invoke(app, ["flakes", "--flaky"]).output
    assert "flaky-one" in out
    assert "always-broken" not in out


def test_flakes_name_filter():
    runner.invoke(app, ["flake", "motor-stop", "-s", "fail"])
    runner.invoke(app, ["flake", "nav-plan", "-s", "fail"])
    out = runner.invoke(app, ["flakes", "--name", "motor"]).output
    assert "motor-stop" in out
    assert "nav-plan" not in out


# ── scaffold ─────────────────────────────────────────────────────────────────


def test_scaffold_save_and_list():
    result = runner.invoke(
        app, ["scaffold", "save", "comp", "--body", "class {{name}} {}"]
    )
    assert result.exit_code == 0
    assert "comp" in result.output
    out = runner.invoke(app, ["scaffold", "list"]).output
    assert "comp" in out
    assert "name" in out  # placeholder surfaced in the listing


def test_scaffold_list_empty():
    out = runner.invoke(app, ["scaffold", "list"]).output
    assert "No scaffolds" in out


def test_scaffold_save_rejects_empty_body():
    result = runner.invoke(app, ["scaffold", "save", "comp", "--body", "   "])
    assert result.exit_code == 1
    assert "empty" in result.output.lower()


def test_scaffold_show():
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "hi {{who}}"])
    out = runner.invoke(app, ["scaffold", "show", "comp"]).output
    assert "hi {{who}}" in out
    assert "who" in out


def test_scaffold_show_missing():
    result = runner.invoke(app, ["scaffold", "show", "nope"])
    assert result.exit_code == 1
    assert "No scaffold" in result.output


def test_scaffold_new_renders_to_file(tmp_path):
    runner.invoke(
        app, ["scaffold", "save", "comp", "--body", "<{{tag}}>hello</{{tag}}>"]
    )
    dest = tmp_path / "out.txt"
    result = runner.invoke(
        app, ["scaffold", "new", "comp", str(dest), "--var", "tag=Spell"]
    )
    assert result.exit_code == 0
    assert dest.read_text() == "<Spell>hello</Spell>"


def test_scaffold_new_reports_missing_holes(tmp_path):
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "{{a}} {{b}}"])
    dest = tmp_path / "out.txt"
    result = runner.invoke(
        app, ["scaffold", "new", "comp", str(dest), "--var", "a=1"]
    )
    assert result.exit_code == 0
    assert "unfilled" in result.output
    assert "b" in result.output
    assert dest.read_text() == "1 {{b}}"  # missing hole left verbatim


def test_scaffold_new_to_stdout():
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "x={{v}}"])
    out = runner.invoke(
        app, ["scaffold", "new", "comp", "--var", "v=42"]
    ).output
    assert "x=42" in out


def test_scaffold_new_refuses_overwrite_without_force(tmp_path):
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "new"])
    dest = tmp_path / "out.txt"
    dest.write_text("existing")
    result = runner.invoke(app, ["scaffold", "new", "comp", str(dest)])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert dest.read_text() == "existing"  # untouched
    forced = runner.invoke(
        app, ["scaffold", "new", "comp", str(dest), "--force"]
    )
    assert forced.exit_code == 0
    assert dest.read_text() == "new"


def test_scaffold_new_bad_var():
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "{{v}}"])
    result = runner.invoke(app, ["scaffold", "new", "comp", "--var", "noequals"])
    assert result.exit_code == 1
    assert "Bad --var" in result.output


def test_scaffold_rm():
    runner.invoke(app, ["scaffold", "save", "comp", "--body", "x"])
    result = runner.invoke(app, ["scaffold", "rm", "comp"])
    assert result.exit_code == 0
    assert runner.invoke(app, ["scaffold", "show", "comp"]).exit_code == 1


def test_scaffold_rm_missing():
    result = runner.invoke(app, ["scaffold", "rm", "nope"])
    assert result.exit_code == 1
    assert "No scaffold" in result.output


def test_scaffold_save_from_file(tmp_path):
    src = tmp_path / "tpl.tsx"
    src.write_text("export const {{name}} = () => null")
    result = runner.invoke(app, ["scaffold", "save", "comp", "--file", str(src)])
    assert result.exit_code == 0
    out = runner.invoke(app, ["scaffold", "show", "comp"]).output
    assert "export const {{name}}" in out
