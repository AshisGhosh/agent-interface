"""Tests for `agi serve` and the static-export mount."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_interface.cli import app as cli_app
from agent_interface.db import _SCHEMA_SQL, _migrate
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.web import create_app, create_app_from_env, mount_static_export


@pytest.fixture
def conn_factory(tmp_path):
    path = tmp_path / "registry.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(_SCHEMA_SQL)
    _migrate(c)
    ensure_schema(c)
    c.close()

    def _factory():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        ensure_schema(c)
        return c

    return _factory


@pytest.fixture
def static_dir(tmp_path):
    """A minimal Next-export-style static tree."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("<html><body>agi ui</body></html>")
    assets = out / "_next" / "static"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log('ui');")
    return out


# ── static mount ─────────────────────────────────────────────────────────────

def test_static_mount_serves_index(conn_factory, static_dir):
    app = create_app(conn_factory=conn_factory, static_dir=str(static_dir))
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "agi ui" in r.text


def test_static_mount_serves_nested_asset(conn_factory, static_dir):
    app = create_app(conn_factory=conn_factory, static_dir=str(static_dir))
    with TestClient(app) as client:
        r = client.get("/_next/static/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text


def test_static_mount_does_not_shadow_api(conn_factory, static_dir):
    """API routes registered before the mount still win precedence."""
    app = create_app(conn_factory=conn_factory, static_dir=str(static_dir))
    with TestClient(app) as client:
        r = client.get("/projects")
        assert r.status_code == 200
        assert r.json() == []

        r = client.get("/openapi.json")
        assert r.status_code == 200
        assert r.json()["info"]["title"] == "agi orchestrator"


def test_mount_static_export_missing_dir_raises(conn_factory, tmp_path):
    app = create_app(conn_factory=conn_factory)
    with pytest.raises(FileNotFoundError):
        mount_static_export(app, str(tmp_path / "does-not-exist"))


def test_create_app_without_static_dir_has_no_ui_mount(conn_factory):
    app = create_app(conn_factory=conn_factory)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 404


def test_create_app_from_env_passes_static_dir(static_dir, monkeypatch):
    """Reload entrypoint reads AGI_STATIC_DIR and forwards it to create_app."""
    monkeypatch.setenv("AGI_STATIC_DIR", str(static_dir))
    captured: dict = {}

    def fake_create_app(static_dir=None):
        captured["static_dir"] = static_dir
        return "sentinel"

    monkeypatch.setattr("agent_interface.web.create_app", fake_create_app)
    assert create_app_from_env() == "sentinel"
    assert captured["static_dir"] == str(static_dir)


def test_create_app_from_env_without_env_passes_none(monkeypatch):
    monkeypatch.delenv("AGI_STATIC_DIR", raising=False)
    captured: dict = {}

    def fake_create_app(static_dir=None):
        captured["static_dir"] = static_dir
        return "sentinel"

    monkeypatch.setattr("agent_interface.web.create_app", fake_create_app)
    create_app_from_env()
    assert captured["static_dir"] is None


# ── CLI: serve command wiring ────────────────────────────────────────────────

runner = CliRunner()


def test_serve_help_lists_flags():
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    for flag in ("--dev", "--ui-port", "--ui-dir", "--static-dir", "--no-ui", "--reload"):
        assert flag in result.output


def test_serve_no_ui_runs_uvicorn_without_static(tmp_path, monkeypatch):
    """--no-ui must skip both static resolution and the dev subprocess."""
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    def fake_run(app_obj, host, port):
        captured["app"] = app_obj
        captured["host"] = host
        captured["port"] = port

    with patch("uvicorn.run", side_effect=fake_run):
        result = runner.invoke(cli_app, ["serve", "--no-ui", "--port", "8001"])

    assert result.exit_code == 0, result.output
    assert captured["port"] == 8001
    # App should have no static "ui" mount registered.
    mount_names = [getattr(r, "name", None) for r in captured["app"].router.routes]
    assert "ui" not in mount_names


def test_serve_default_warns_when_static_missing(tmp_path, monkeypatch):
    """Default (non-dev) mode prints a warning if ui/out doesn't exist."""
    monkeypatch.chdir(tmp_path)

    with patch("uvicorn.run") as mock_run:
        result = runner.invoke(cli_app, ["serve", "--port", "8002"])

    assert result.exit_code == 0, result.output
    assert "No static export" in result.output
    assert mock_run.called


def test_serve_default_resolves_existing_static_dir(tmp_path, monkeypatch, static_dir):
    """If ui/out exists it's passed into create_app as static_dir."""
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "out").symlink_to(static_dir)
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    def fake_run(app_obj, host, port):
        captured["app"] = app_obj

    with patch("uvicorn.run", side_effect=fake_run):
        result = runner.invoke(cli_app, ["serve"])

    assert result.exit_code == 0, result.output
    # The returned FastAPI app should have the static mount present.
    app_obj = captured["app"]
    mount_names = [getattr(r, "name", None) for r in app_obj.router.routes]
    assert "ui" in mount_names


def test_serve_dev_requires_ui_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no ui/ here
    with patch("uvicorn.run"):
        result = runner.invoke(cli_app, ["serve", "--dev"])
    assert result.exit_code == 1
    assert "No package.json" in result.output


def test_serve_dev_spawns_npm_and_uvicorn(tmp_path, monkeypatch):
    """--dev launches `npm run dev` in ui/ and then runs uvicorn."""
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "package.json").write_text("{}")
    monkeypatch.chdir(tmp_path)

    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.waited = False

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def kill(self):
            self.terminated = True

    fake_proc = FakeProc()

    popen_calls: list = []

    def fake_popen(cmd, cwd=None, env=None):
        popen_calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        return fake_proc

    uvicorn_calls: list = []

    def fake_uvicorn_run(app_obj, host, port):
        uvicorn_calls.append({"host": host, "port": port})

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch("uvicorn.run", side_effect=fake_uvicorn_run), \
         patch("shutil.which", return_value="/usr/bin/npm"):
        result = runner.invoke(cli_app, ["serve", "--dev", "--ui-port", "3333"])

    assert result.exit_code == 0, result.output
    assert popen_calls, "npm run dev was not invoked"
    call = popen_calls[0]
    assert call["cmd"][0] == "/usr/bin/npm"
    assert "run" in call["cmd"] and "dev" in call["cmd"]
    assert "--port" in call["cmd"] and "3333" in call["cmd"]
    assert Path(call["cwd"]).resolve() == ui.resolve()
    assert call["env"]["AGI_API_URL"] == "http://127.0.0.1:8000"

    assert uvicorn_calls and uvicorn_calls[0]["port"] == 8000
    # Cleanup path should have terminated the Next child.
    assert fake_proc.terminated is True
    assert fake_proc.waited is True
