"""Tests for workflow discovery from session history."""

from agent_interface.insights import WorkflowOpportunity, _tokenize, analyze_sessions
from agent_interface.models import Session
from agent_interface.registry import register_session


def _labelled(id, repo, label):
    return Session(id=id, state="done", repo_root=repo, cwd=repo, label=label)


def test_tokenize_drops_stopwords_and_short():
    toks = _tokenize("Fix the retry logic in scoring")
    assert "retry" in toks
    assert "scoring" in toks
    assert "the" not in toks
    assert "in" not in toks


def test_analyze_groups_by_repo_and_thresholds(conn):
    for i in range(4):
        register_session(conn, _labelled(f"a{i}", "/repo/alpha", f"fix retry bug {i}"))
    # Below threshold — should be excluded.
    register_session(conn, _labelled("b0", "/repo/beta", "one off thing"))

    opps = analyze_sessions(conn, min_sessions=3)

    repos = [o.repo for o in opps]
    assert "/repo/alpha" in repos
    assert "/repo/beta" not in repos


def test_analyze_extracts_keywords(conn):
    for i in range(3):
        register_session(conn, _labelled(f"a{i}", "/repo/alpha", "deploy the staging server"))

    opps = analyze_sessions(conn, min_sessions=3)
    kw = dict(opps[0].keywords)
    assert "deploy" in kw
    assert "staging" in kw
    assert kw["deploy"] == 3  # counted once per label, across 3 labels


def test_analyze_ignores_unlabelled(conn):
    register_session(conn, Session(id="x", state="done", repo_root="/repo/x", label=None))
    register_session(conn, Session(id="y", state="done", repo_root="/repo/x", label=""))
    opps = analyze_sessions(conn, min_sessions=1)
    assert opps == []


def test_score_prefers_concentrated_work():
    focused = WorkflowOpportunity(
        repo="/a", session_count=5, keywords=[("deploy", 5), ("ci", 4), ("fix", 3)],
    )
    scattered = WorkflowOpportunity(
        repo="/b", session_count=5, keywords=[("x", 1), ("y", 1), ("z", 1)],
    )
    assert focused.score > scattered.score


def test_worktrees_collapse_to_parent_repo(conn):
    register_session(conn, _labelled("a0", "/repo/proj", "deploy ci"))
    register_session(conn, _labelled("a1", "/repo/proj/.worktrees/t-abc123", "deploy ci"))
    register_session(conn, _labelled("a2", "/repo/proj/.worktrees/t-def456", "deploy ci"))

    opps = analyze_sessions(conn, min_sessions=3)
    assert len(opps) == 1
    assert opps[0].repo == "/repo/proj"
    assert opps[0].session_count == 3


def test_non_project_paths_excluded(conn):
    for i in range(4):
        register_session(conn, _labelled(
            f"v{i}", "/home/u/.local/share/uv/tools/x/lib/python3.12", "do thing",
        ))
    assert analyze_sessions(conn, min_sessions=3) == []


def test_task_ids_and_boilerplate_filtered():
    toks = _tokenize("autonomous agent working on t-8a19ca73 deploy pipeline")
    assert "deploy" in toks and "pipeline" in toks
    assert "autonomous" not in toks
    assert "agent" not in toks
    assert "t-8a19ca73" not in toks


def test_results_sorted_by_score(conn):
    for i in range(5):
        register_session(conn, _labelled(f"a{i}", "/repo/big", "deploy ci pipeline"))
    for i in range(3):
        register_session(conn, _labelled(f"c{i}", "/repo/small", "misc chore work"))

    opps = analyze_sessions(conn, min_sessions=3)
    assert [o.repo for o in opps] == sorted(
        [o.repo for o in opps], key=lambda r: {"/repo/big": 0, "/repo/small": 1}[r],
    )
    assert opps[0].repo == "/repo/big"
