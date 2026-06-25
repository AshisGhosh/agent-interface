"""Tests for the iteration assessment rubric (agi assess / agi assessments)."""

import os
import sqlite3

import pytest

# Point DB at a temp location before importing the app (mirrors test_cli.py).
os.environ.setdefault("AGI_DB_PATH", ":memory:")

from typer.testing import CliRunner

from agent_interface.assess import (
    assessment_trend,
    latest_assessment,
    list_assessments,
    next_iteration,
    parse_criterion,
    project_key,
    record_assessment,
    remove_assessment,
)
from agent_interface.cli import app

runner = CliRunner()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── parse_criterion ──────────────────────────────────────────────────────────


def test_parse_criterion_ok():
    assert parse_criterion("lighting=7") == ("lighting", 7.0)
    assert parse_criterion(" palette = 5.5 ") == ("palette", 5.5)


def test_parse_criterion_rejects_missing_eq():
    with pytest.raises(ValueError):
        parse_criterion("lighting")


def test_parse_criterion_rejects_empty_name():
    with pytest.raises(ValueError):
        parse_criterion("=7")


def test_parse_criterion_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_criterion("lighting=great")


# ── record / list ────────────────────────────────────────────────────────────


def test_record_assigns_incrementing_iteration(conn):
    r1 = record_assessment(conn, project="/p", subject="art", scores={"x": 1})
    r2 = record_assessment(conn, project="/p", subject="art", scores={"x": 2})
    assert r1["iteration"] == 1
    assert r2["iteration"] == 2


def test_iteration_is_per_subject(conn):
    record_assessment(conn, project="/p", subject="art", scores={"x": 1})
    other = record_assessment(conn, project="/p", subject="audio", scores={"x": 1})
    assert other["iteration"] == 1  # different subject restarts the count


def test_next_iteration_preview(conn):
    assert next_iteration(conn, "/p", "art") == 1
    record_assessment(conn, project="/p", subject="art", scores={"x": 1})
    assert next_iteration(conn, "/p", "art") == 2


def test_list_attaches_scores_in_order(conn):
    record_assessment(
        conn,
        project="/p",
        subject="art",
        scores=[("lighting", 7), ("palette", 5)],
        verdict="needs-work",
        note="too dark",
        created_at=1.0,
    )
    rows = list_assessments(conn, "/p")
    assert len(rows) == 1
    a = rows[0]
    assert a["subject"] == "art"
    assert a["iteration"] == 1
    assert a["verdict"] == "needs-work"
    assert a["note"] == "too dark"
    assert a["scores"] == [("lighting", 7.0), ("palette", 5.0)]


def test_list_newest_first(conn):
    record_assessment(conn, project="/p", subject="art", scores={"x": 1},
                      created_at=100.0)
    record_assessment(conn, project="/p", subject="art", scores={"x": 2},
                      created_at=200.0)
    rows = list_assessments(conn, "/p")
    assert [a["iteration"] for a in rows] == [2, 1]


def test_list_is_project_scoped(conn):
    record_assessment(conn, project="/a", subject="art", scores={"x": 1})
    record_assessment(conn, project="/b", subject="art", scores={"x": 1})
    assert len(list_assessments(conn, "/a")) == 1
    assert len(list_assessments(conn, "/b")) == 1


def test_list_subject_filter(conn):
    record_assessment(conn, project="/p", subject="art", scores={"x": 1})
    record_assessment(conn, project="/p", subject="audio", scores={"x": 1})
    rows = list_assessments(conn, "/p", subject="audio")
    assert [a["subject"] for a in rows] == ["audio"]


def test_record_allows_verdict_only(conn):
    r = record_assessment(conn, project="/p", subject="art", verdict="ship")
    rows = list_assessments(conn, "/p")
    assert rows[0]["verdict"] == "ship"
    assert rows[0]["scores"] == []
    assert r["scores"] == []


def test_repeated_criterion_last_wins(conn):
    record_assessment(
        conn, project="/p", subject="art",
        scores=[("lighting", 3), ("lighting", 9)],
    )
    assert list_assessments(conn, "/p")[0]["scores"] == [("lighting", 9.0)]


def test_latest_assessment(conn):
    record_assessment(conn, project="/p", subject="art", scores={"x": 1},
                      created_at=1.0)
    record_assessment(conn, project="/p", subject="art", scores={"x": 2},
                      created_at=2.0)
    assert latest_assessment(conn, "/p", "art")["iteration"] == 2
    assert latest_assessment(conn, "/p", "missing") is None


# ── trend ────────────────────────────────────────────────────────────────────


def test_trend_tracks_per_criterion_progression(conn):
    record_assessment(conn, project="/p", subject="art",
                      scores={"lighting": 4, "palette": 8}, created_at=1.0)
    record_assessment(conn, project="/p", subject="art",
                      scores={"lighting": 7, "palette": 5}, created_at=2.0)
    trend = assessment_trend(conn, "/p", "art")
    by = {e["criterion"]: e for e in trend}
    assert by["lighting"]["points"] == [(1, 4.0), (2, 7.0)]
    assert by["lighting"]["delta"] == 3.0
    assert by["lighting"]["direction"] == "up"
    assert by["palette"]["delta"] == -3.0
    assert by["palette"]["direction"] == "down"


def test_trend_single_point_is_flat(conn):
    record_assessment(conn, project="/p", subject="art", scores={"x": 5})
    trend = assessment_trend(conn, "/p", "art")
    assert trend[0]["direction"] == "flat"
    assert trend[0]["delta"] == 0.0


def test_trend_is_alphabetical(conn):
    record_assessment(conn, project="/p", subject="art",
                      scores={"zeta": 1, "alpha": 1})
    assert [e["criterion"] for e in assessment_trend(conn, "/p", "art")] == [
        "alpha", "zeta",
    ]


def test_trend_empty_for_unknown_subject(conn):
    assert assessment_trend(conn, "/p", "nope") == []


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove_deletes_assessment_and_scores(conn):
    r = record_assessment(conn, project="/p", subject="art", scores={"x": 1})
    assert remove_assessment(conn, "/p", r["id"]) is True
    assert list_assessments(conn, "/p") == []
    # scores rows are gone too
    left = conn.execute("SELECT COUNT(*) FROM assessment_scores").fetchone()[0]
    assert left == 0
    # gone now → removing again is a no-op
    assert remove_assessment(conn, "/p", r["id"]) is False


def test_remove_is_project_scoped(conn):
    r = record_assessment(conn, project="/a", subject="art", scores={"x": 1})
    assert remove_assessment(conn, "/b", r["id"]) is False
    assert len(list_assessments(conn, "/a")) == 1


# ── project key ──────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())


# ── CLI ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "test.db"))
    # Keep all assess invocations keyed to one stable project.
    monkeypatch.chdir(tmp_path)


def test_cli_assess_and_list(_fresh_db):
    result = runner.invoke(
        app, ["assess", "art-overhaul", "-c", "lighting=7", "-c", "palette=5",
               "-V", "needs-work"],
    )
    assert result.exit_code == 0, result.output
    assert "iter 1" in result.output

    listed = runner.invoke(app, ["assessments"])
    assert listed.exit_code == 0
    assert "art-overhaul" in listed.output
    assert "lighting" in listed.output


def test_cli_iteration_increments(_fresh_db):
    runner.invoke(app, ["assess", "art", "-c", "x=1"])
    second = runner.invoke(app, ["assess", "art", "-c", "x=2"])
    assert "iter 2" in second.output


def test_cli_requires_subject(_fresh_db):
    result = runner.invoke(app, ["assess"])
    assert result.exit_code == 1
    assert "No subject" in result.output


def test_cli_requires_some_content(_fresh_db):
    result = runner.invoke(app, ["assess", "art"])
    assert result.exit_code == 1
    assert "Nothing to assess" in result.output


def test_cli_rejects_bad_criterion(_fresh_db):
    result = runner.invoke(app, ["assess", "art", "-c", "lighting"])
    assert result.exit_code == 1


def test_cli_trend(_fresh_db):
    runner.invoke(app, ["assess", "art", "-c", "lighting=4"])
    runner.invoke(app, ["assess", "art", "-c", "lighting=7"])
    result = runner.invoke(app, ["assessments", "art", "--trend"])
    assert result.exit_code == 0
    assert "lighting" in result.output
    assert "+3" in result.output


def test_cli_trend_needs_subject(_fresh_db):
    result = runner.invoke(app, ["assessments", "--trend"])
    assert result.exit_code == 1
    assert "needs a subject" in result.output


def test_cli_rm(_fresh_db):
    runner.invoke(app, ["assess", "art", "-c", "x=1"])
    result = runner.invoke(app, ["assessments", "--rm", "1"])
    assert result.exit_code == 0
    assert "removed assessment #1" in result.output
    missing = runner.invoke(app, ["assessments", "--rm", "1"])
    assert missing.exit_code == 1


def test_cli_records_usage(_fresh_db, monkeypatch):
    calls = []
    import agent_interface.usage as usage

    monkeypatch.setattr(usage, "record_usage",
                        lambda fid, **kw: calls.append(fid))
    runner.invoke(app, ["assess", "art", "-c", "x=1"])
    assert "feat-3a801c34" in calls
