"""Tests for the per-project cluster/remote job ledger (agi job / agi jobs)."""

import sqlite3

import pytest

from agent_interface.jobs import (
    add_job,
    get_job,
    list_jobs,
    project_key,
    remove_job,
    update_job,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── add / list ─────────────────────────────────────────────────────────────────


def test_add_and_list(conn):
    add_job(conn, project="/p", title="warmup", now=100.0)
    add_job(
        conn,
        project="/p",
        title="H100 sweep",
        job_id="481923",
        aim="https://aim/run/ab12",
        now=200.0,
    )
    jobs = list_jobs(conn, "/p")
    assert [j["title"] for j in jobs] == ["H100 sweep", "warmup"]  # newest first
    top = jobs[0]
    assert top["job_id"] == "481923"
    assert top["aim"] == "https://aim/run/ab12"
    assert top["status"] == "submitted"  # default


def test_jobs_are_project_scoped(conn):
    add_job(conn, project="/a", title="alpha", now=1.0)
    add_job(conn, project="/b", title="beta", now=1.0)
    assert [j["title"] for j in list_jobs(conn, "/a")] == ["alpha"]
    assert [j["title"] for j in list_jobs(conn, "/b")] == ["beta"]


def test_status_filter(conn):
    add_job(conn, project="/p", title="one", status="running", now=1.0)
    add_job(conn, project="/p", title="two", status="done", now=2.0)
    running = list_jobs(conn, "/p", status="running")
    assert [j["title"] for j in running] == ["one"]


def test_open_only_filter(conn):
    add_job(conn, project="/p", title="sub", status="submitted", now=1.0)
    add_job(conn, project="/p", title="run", status="running", now=2.0)
    add_job(conn, project="/p", title="fin", status="done", now=3.0)
    add_job(conn, project="/p", title="bad", status="failed", now=4.0)
    open_jobs = list_jobs(conn, "/p", open_only=True)
    assert {j["title"] for j in open_jobs} == {"sub", "run"}


def test_limit(conn):
    for i in range(5):
        add_job(conn, project="/p", title=f"j{i}", now=float(i))
    assert len(list_jobs(conn, "/p", limit=3)) == 3


def test_list_orders_by_update_time(conn):
    a = add_job(conn, project="/p", title="a", now=1.0)
    add_job(conn, project="/p", title="b", now=2.0)
    # Touching 'a' should float it to the top despite being created first.
    update_job(conn, "/p", a, status="running", now=3.0)
    assert [j["title"] for j in list_jobs(conn, "/p")] == ["a", "b"]


# ── update ───────────────────────────────────────────────────────────────────


def test_update_patches_only_given_fields(conn):
    jid = add_job(conn, project="/p", title="job", job_id="1", aim="run-x", now=1.0)
    assert update_job(conn, "/p", jid, status="running", now=2.0) is True
    row = get_job(conn, "/p", jid)
    assert row["status"] == "running"
    assert row["job_id"] == "1"  # untouched
    assert row["aim"] == "run-x"  # untouched
    assert row["updated_at"] == 2.0


def test_update_no_fields_is_noop(conn):
    jid = add_job(conn, project="/p", title="job", now=1.0)
    assert update_job(conn, "/p", jid) is False


def test_update_is_project_scoped(conn):
    jid = add_job(conn, project="/a", title="keep", status="submitted", now=1.0)
    # an id from /a cannot be patched via project /b
    assert update_job(conn, "/b", jid, status="done") is False
    assert get_job(conn, "/a", jid)["status"] == "submitted"


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove(conn):
    jid = add_job(conn, project="/p", title="temp", now=1.0)
    assert remove_job(conn, "/p", jid) is True
    assert list_jobs(conn, "/p") == []
    assert remove_job(conn, "/p", jid) is False  # gone now


def test_remove_is_project_scoped(conn):
    jid = add_job(conn, project="/a", title="keep", now=1.0)
    assert remove_job(conn, "/b", jid) is False
    assert [j["title"] for j in list_jobs(conn, "/a")] == ["keep"]


# ── project key ─────────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
