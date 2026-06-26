"""Tests for first-class project dashboards (declare → keep up → open)."""

import time

import pytest

import agent_interface.daemon as daemon
import agent_interface.dashboards as dashboards


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "reg.db"))
    monkeypatch.setattr(daemon, "LOG_DIR", tmp_path / "daemons")
    # cwd is its own "repo"; use real dirs so detached launches can chdir.
    monkeypatch.setattr(daemon, "_repo_key", lambda cwd: cwd or str(tmp_path))
    pa, pb = tmp_path / "projA", tmp_path / "projB"
    pa.mkdir()
    pb.mkdir()
    return str(pa), str(pb)


def test_declare_and_list_scoped_by_project(env):
    pa, pb = env
    dashboards.declare("web", ["sleep", "5"], url="http://localhost:3000", cwd=pa)
    dashboards.declare("notebook", ["sleep", "5"], cwd=pb)

    a = dashboards.list_dashboards(cwd=pa)
    assert [d["name"] for d in a] == ["web"]
    assert a[0]["url"] == "http://localhost:3000"
    assert a[0]["status"] == "down"  # declared, not started

    allp = dashboards.list_dashboards(all_projects=True)
    assert {d["name"] for d in allp} == {"web", "notebook"}


def test_up_starts_and_is_idempotent(env):
    pa, _ = env
    dashboards.declare("web", ["sleep", "10"], cwd=pa)
    r1 = dashboards.up("web", cwd=pa)
    assert r1[0]["status"] == "started"
    assert dashboards.list_dashboards(cwd=pa)[0]["status"] == "up"
    # Second up() doesn't relaunch.
    r2 = dashboards.up("web", cwd=pa)
    assert r2[0]["status"] == "already up"
    daemon.stop("web", cwd=pa)


def test_ensure_up_restarts_crashed_supervised(env):
    pa, _ = env
    dashboards.declare("web", ["sleep", "10"], cwd=pa, supervised=True)
    dashboards.up("web", cwd=pa)
    daemon.stop("web", cwd=pa)  # simulate a crash
    time.sleep(0.2)
    assert dashboards.list_dashboards(cwd=pa)[0]["status"] == "down"

    out = dashboards.ensure_up()
    assert any("web@" in r for r in out["restarted"])
    assert dashboards.list_dashboards(cwd=pa)[0]["status"] == "up"
    daemon.stop("web", cwd=pa)


def test_ensure_up_skips_unsupervised(env):
    pa, _ = env
    dashboards.declare("web", ["sleep", "10"], cwd=pa, supervised=False)
    out = dashboards.ensure_up()  # never started, but unsupervised → not restarted
    assert out["restarted"] == []
    assert dashboards.list_dashboards(cwd=pa)[0]["status"] == "down"


def test_remove(env):
    pa, _ = env
    dashboards.declare("web", ["sleep", "5"], cwd=pa)
    assert dashboards.remove("web", cwd=pa) is True
    assert dashboards.list_dashboards(cwd=pa) == []
    assert dashboards.remove("web", cwd=pa) is False


def test_get_returns_url_and_cmd(env):
    pa, _ = env
    dashboards.declare("web", ["npm", "run", "dev"], url="http://x", cwd=pa)
    d = dashboards.get("web", cwd=pa)
    assert d["url"] == "http://x"
    assert d["cmd"] == "npm run dev"
    assert dashboards.get("missing", cwd=pa) is None
