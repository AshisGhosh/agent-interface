"""Tests for the delivery safety-net — ensuring completed self-improve work
actually lands on main (delivered AND used), not stranded on a branch."""

import sqlite3

import pytest

import agent_interface.optimizer as opt


@pytest.fixture
def orch_conn():
    """Minimal projects+tasks schema for delivery detection."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT, status TEXT);
        """
    )
    c.execute("INSERT INTO projects VALUES ('p1', 'agi-self-improve')")
    c.execute("INSERT INTO projects VALUES ('p2', 'other')")
    return c


def _add_task(conn, id, status, project="p1", title="improve thing"):
    conn.execute("INSERT INTO tasks VALUES (?,?,?,?)", (id, project, title, status))


def _fake_git(branch_exists, in_main):
    """Build a _git stub keyed on the git subcommand."""
    def run(args, repo, timeout=120):
        class R:
            returncode = 0
            stdout = ""
        r = R()
        if args[:2] == ["rev-parse", "--verify"]:
            r.returncode = 0 if branch_exists else 1
        elif args[:1] == ["merge-base"]:
            # ["merge-base","--is-ancestor", X, Y]
            y = args[3]
            if y == "main":           # is branch already in main?
                r.returncode = 0 if in_main else 1
            else:                      # is main an ancestor of branch (ff-able)?
                r.returncode = 0
        return r
    return run


# ── detection ─────────────────────────────────────────────────────────────────


def test_pending_lists_unmerged_done_tasks(orch_conn, monkeypatch):
    _add_task(orch_conn, "t-aaa", "done")
    monkeypatch.setattr(opt, "_git", _fake_git(branch_exists=True, in_main=False))

    pend = opt.pending_deliveries(orch_conn, "/repo")
    assert [p["id"] for p in pend] == ["t-aaa"]
    assert pend[0]["branch"] == "task/t-aaa"


def test_pending_skips_already_merged(orch_conn, monkeypatch):
    _add_task(orch_conn, "t-aaa", "done")
    monkeypatch.setattr(opt, "_git", _fake_git(branch_exists=True, in_main=True))
    assert opt.pending_deliveries(orch_conn, "/repo") == []


def test_pending_skips_missing_branch(orch_conn, monkeypatch):
    _add_task(orch_conn, "t-aaa", "done")
    monkeypatch.setattr(opt, "_git", _fake_git(branch_exists=False, in_main=False))
    assert opt.pending_deliveries(orch_conn, "/repo") == []


def test_pending_ignores_non_done_and_other_projects(orch_conn, monkeypatch):
    _add_task(orch_conn, "t-open", "in_progress")
    _add_task(orch_conn, "t-other", "done", project="p2")
    monkeypatch.setattr(opt, "_git", _fake_git(branch_exists=True, in_main=False))
    assert opt.pending_deliveries(orch_conn, "/repo") == []


# ── safety: never touch a busy / off-main repo ────────────────────────────────


def test_deliver_flags_when_repo_not_idle(orch_conn, monkeypatch, tmp_path):
    _add_task(orch_conn, "t-aaa", "done")
    monkeypatch.setattr(opt, "_git", _fake_git(branch_exists=True, in_main=False))
    monkeypatch.setattr(opt, "_repo_idle_on_main", lambda repo: False)
    monkeypatch.setattr(opt, "AUDIT_PATH", tmp_path / "a.log")

    import agent_interface.orchestrator.db as odb
    monkeypatch.setattr(odb, "get_connection", lambda: orch_conn)

    res = opt.deliver_pending(repo="/repo", notify=False)
    assert res["landed"] == []
    assert len(res["flagged"]) == 1
    assert "busy" in res["flagged"][0]["reason"]


def test_deliver_lands_when_ff_and_green(orch_conn, monkeypatch, tmp_path):
    _add_task(orch_conn, "t-aaa", "done")
    calls = []

    def rec_git(args, repo, timeout=120):
        calls.append(args)
        class R:
            returncode = 0
            stdout = "main" if args[:1] == ["symbolic-ref"] else ""
        return R()

    monkeypatch.setattr(opt, "_git", rec_git)
    monkeypatch.setattr(opt, "pending_deliveries",
                        lambda conn, repo: [{"id": "t-aaa", "title": "x", "branch": "task/t-aaa"}])
    monkeypatch.setattr(opt, "_repo_idle_on_main", lambda repo: True)
    monkeypatch.setattr(opt, "_preflight_ok", lambda repo: True)
    monkeypatch.setattr(opt, "AUDIT_PATH", tmp_path / "a.log")
    import agent_interface.orchestrator.db as odb
    monkeypatch.setattr(odb, "get_connection", lambda: orch_conn)

    res = opt.deliver_pending(repo="/repo", notify=False)
    assert [i["id"] for i in res["landed"]] == ["t-aaa"]
    # It fast-forward-merged and deleted the merged branch.
    assert ["merge", "--ff-only", "task/t-aaa"] in calls
    assert ["branch", "-d", "task/t-aaa"] in calls


def test_deliver_rolls_back_on_test_failure(orch_conn, monkeypatch, tmp_path):
    _add_task(orch_conn, "t-aaa", "done")
    calls = []

    def rec_git(args, repo, timeout=120):
        calls.append(args)
        class R:
            returncode = 0
            stdout = "deadbeef" if args[:1] == ["rev-parse"] else ""
        return R()

    monkeypatch.setattr(opt, "_git", rec_git)
    monkeypatch.setattr(opt, "pending_deliveries",
                        lambda conn, repo: [{"id": "t-aaa", "title": "x", "branch": "task/t-aaa"}])
    monkeypatch.setattr(opt, "_repo_idle_on_main", lambda repo: True)
    monkeypatch.setattr(opt, "_preflight_ok", lambda repo: False)  # tests fail
    monkeypatch.setattr(opt, "AUDIT_PATH", tmp_path / "a.log")
    import agent_interface.orchestrator.db as odb
    monkeypatch.setattr(odb, "get_connection", lambda: orch_conn)

    res = opt.deliver_pending(repo="/repo", notify=False)
    assert res["landed"] == []
    assert "rolled back" in res["flagged"][0]["reason"]
    # Rolled main back to the pre-merge sha.
    assert ["reset", "--hard", "deadbeef"] in calls
