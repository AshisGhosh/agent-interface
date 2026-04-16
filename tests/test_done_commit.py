"""Tests for auto-commit on done and the review mechanism."""

import subprocess as sp
from pathlib import Path

import pytest

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.orchestrator.states import TaskStatus


@pytest.fixture
def oconn(conn):
    ensure_schema(conn)
    return conn


@pytest.fixture
def git_worktree(tmp_path: Path):
    """Create a fresh git repo with an initial commit. Returns the path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("init\n")
    sp.run(["git", "add", "."], cwd=repo, check=True)
    sp.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# ── commit on done ──────────────────────────────────────────────────────────

def test_done_commits_worktree_changes(oconn, git_worktree):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "build feature")

    # Attach worktree path + uncommitted change.
    oconn.execute(
        "UPDATE tasks SET worktree_path=? WHERE id=?",
        (str(git_worktree), t.id),
    )
    oconn.commit()
    (git_worktree / "new.txt").write_text("hello\n")

    result = core.done_task(oconn, t.id, summary="added new.txt")

    assert result.status == TaskStatus.DONE

    # The commit should have happened.
    log = sp.run(
        ["git", "log", "--oneline"], cwd=git_worktree,
        capture_output=True, text=True, check=True,
    )
    assert "build feature" in log.stdout  # title is in commit msg
    assert "init" in log.stdout


def test_done_without_changes_still_done(oconn, git_worktree):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "nothing")
    oconn.execute(
        "UPDATE tasks SET worktree_path=? WHERE id=?",
        (str(git_worktree), t.id),
    )
    oconn.commit()

    result = core.done_task(oconn, t.id, summary="no changes")
    assert result.status == TaskStatus.DONE


def test_done_commit_failure_moves_to_review(oconn, git_worktree):
    """If a pre-commit hook fails, task goes to review with error payload."""
    # Install a pre-commit hook that always fails.
    hook = git_worktree / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'forced failure' >&2\nexit 1\n")
    hook.chmod(0o755)

    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "thing")
    oconn.execute(
        "UPDATE tasks SET worktree_path=? WHERE id=?",
        (str(git_worktree), t.id),
    )
    oconn.commit()
    (git_worktree / "x.txt").write_text("new\n")

    result = core.done_task(oconn, t.id, summary="tried to finish")

    assert result.status == TaskStatus.REVIEW

    # review_requested event recorded with the error.
    events = core.list_events(oconn, t.id)
    review_events = [e for e in events if e.event_type == "review_requested"]
    assert len(review_events) == 1
    assert "forced failure" in review_events[0].payload_json


def test_done_no_worktree_just_marks_done(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "no-worktree task")
    result = core.done_task(oconn, t.id, summary="ok")
    assert result.status == TaskStatus.DONE


# ── review ──────────────────────────────────────────────────────────────────

def test_approve_review_closes_task(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    # Manually put in review.
    oconn.execute("UPDATE tasks SET status='review' WHERE id=?", (t.id,))
    oconn.commit()

    result = core.approve_review(oconn, t.id)
    assert result.status == TaskStatus.DONE

    events = core.list_events(oconn, t.id)
    assert any(e.event_type == "approved" for e in events)


def test_reject_review_returns_to_in_progress(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    oconn.execute(
        "UPDATE tasks SET status='review', assigned_session_id='s1' WHERE id=?",
        (t.id,),
    )
    oconn.commit()

    result = core.reject_review(oconn, t.id, reason="missed a file")
    assert result.status == TaskStatus.IN_PROGRESS


def test_reject_unassigned_returns_to_ready(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")
    oconn.execute(
        "UPDATE tasks SET status='review', assigned_session_id=NULL WHERE id=?",
        (t.id,),
    )
    oconn.commit()

    result = core.reject_review(oconn, t.id, reason="wrong approach")
    assert result.status == TaskStatus.READY


def test_approve_rejects_non_review(oconn):
    core.create_project(oconn, "p1")
    t = core.add_task(oconn, "p1", "x")  # ready
    with pytest.raises(ValueError):
        core.approve_review(oconn, t.id)


def test_done_auto_promote_works_with_commit_path(oconn, git_worktree):
    """Full flow: parent done (with commit) → child auto-promotes."""
    core.create_project(oconn, "p1")
    a = core.add_task(oconn, "p1", "parent")
    b = core.add_task(oconn, "p1", "child", depends_on=[a.id])

    oconn.execute(
        "UPDATE tasks SET worktree_path=? WHERE id=?",
        (str(git_worktree), a.id),
    )
    oconn.commit()
    (git_worktree / "parent-out.txt").write_text("ok\n")

    core.done_task(oconn, a.id, summary="parent shipped")

    # Child should have auto-promoted.
    assert core.get_task(oconn, b.id).status == TaskStatus.READY
