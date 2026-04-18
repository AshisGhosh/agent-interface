"""Tests for the FastAPI backend."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_interface.db import _SCHEMA_SQL, _migrate
from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import ensure_schema
from agent_interface.web import create_app


@pytest.fixture
def db_path(tmp_path):
    """Real on-disk SQLite file so multiple connections see the same data."""
    path = tmp_path / "registry.db"

    # Prime the schema once using the same helpers the app will call later.
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    _migrate(conn)
    ensure_schema(conn)
    conn.close()
    return path


@pytest.fixture
def conn_factory(db_path):
    def _factory():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        ensure_schema(c)
        return c
    return _factory


@pytest.fixture
def client(conn_factory):
    app = create_app(conn_factory=conn_factory)
    with TestClient(app) as c:
        yield c


# ── projects ─────────────────────────────────────────────────────────────────

def test_create_and_list_projects(client):
    r = client.post("/projects", json={"name": "web-ui", "description": "ship it"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "web-ui"
    assert body["id"].startswith("p-")

    r = client.get("/projects")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "web-ui"


def test_create_project_conflict_on_duplicate(client):
    client.post("/projects", json={"name": "dupe"})
    r = client.post("/projects", json={"name": "dupe"})
    assert r.status_code == 409


# ── tasks ────────────────────────────────────────────────────────────────────

def test_create_task_and_list_in_project(client):
    client.post("/projects", json={"name": "p1"})

    r = client.post("/tasks", json={"project": "p1", "title": "first", "priority": 1})
    assert r.status_code == 201
    task = r.json()
    assert task["title"] == "first"
    assert task["status"] == "ready"

    r = client.get("/projects/p1/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task["id"]


def test_list_tasks_unknown_project_404(client):
    r = client.get("/projects/does-not-exist/tasks")
    assert r.status_code == 404


def test_list_tasks_includes_latest_progress_pct(client, conn_factory):
    client.post("/projects", json={"name": "p1"})
    with_progress = client.post(
        "/tasks", json={"project": "p1", "title": "has progress"}
    ).json()["id"]
    without_progress = client.post(
        "/tasks", json={"project": "p1", "title": "no progress"}
    ).json()["id"]

    conn = conn_factory()
    try:
        core.progress(conn, with_progress, "first", pct=10)
        core.progress(conn, with_progress, "second", pct=72)
        core.progress(conn, without_progress, "note only")  # no pct
    finally:
        conn.close()

    tasks = {t["id"]: t for t in client.get("/projects/p1/tasks").json()}
    assert tasks[with_progress]["progress_pct"] == 72
    assert tasks[without_progress]["progress_pct"] is None


def test_create_task_unknown_project_400(client):
    r = client.post("/tasks", json={"project": "nope", "title": "x"})
    assert r.status_code == 400


def test_patch_task_priority(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.patch(f"/tasks/{tid}", json={"priority": 0})
    assert r.status_code == 200
    assert r.json()["priority"] == 0


def test_patch_task_assignment(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.patch(f"/tasks/{tid}", json={"assigned_session_id": "s-abc"})
    assert r.status_code == 200
    assert r.json()["assigned_session_id"] == "s-abc"

    r = client.patch(f"/tasks/{tid}", json={"clear_assignment": True})
    assert r.status_code == 200
    assert r.json()["assigned_session_id"] is None


def test_patch_task_status_done(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.patch(f"/tasks/{tid}", json={"status": "done", "done_summary": "shipped"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_patch_task_status_block_and_unblock(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.patch(
        f"/tasks/{tid}",
        json={"status": "blocked", "block_reason": "need creds", "block_needs": "resource"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "blocked"

    r = client.patch(f"/tasks/{tid}", json={"status": "ready"})
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_patch_task_invalid_status(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.patch(f"/tasks/{tid}", json={"status": "nope"})
    assert r.status_code == 400


def test_patch_task_not_found(client):
    r = client.patch("/tasks/t-missing", json={"priority": 1})
    assert r.status_code == 404


def test_delete_task(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    r = client.delete(f"/tasks/{tid}")
    assert r.status_code == 204

    r = client.get("/projects/p1/tasks")
    assert r.json() == []


def test_delete_task_not_found(client):
    r = client.delete("/tasks/t-missing")
    assert r.status_code == 404


def test_delete_task_refuses_when_dependents_exist(client, conn_factory):
    client.post("/projects", json={"name": "p1"})
    a = client.post("/tasks", json={"project": "p1", "title": "a"}).json()["id"]
    # Add a dependent task via core so we can set up the dep link cleanly.
    conn = conn_factory()
    core.add_task(conn, "p1", "b", depends_on=[a])
    conn.close()

    r = client.delete(f"/tasks/{a}")
    assert r.status_code == 409


# ── task events ──────────────────────────────────────────────────────────────

def test_task_events_endpoint(client):
    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]

    client.patch(f"/tasks/{tid}", json={"priority": 0})
    r = client.get(f"/tasks/{tid}/events")
    assert r.status_code == 200
    events = r.json()
    types = [e["event_type"] for e in events]
    assert types[0] == "created"
    assert "updated" in types


def test_task_events_unknown_task(client):
    r = client.get("/tasks/t-missing/events")
    assert r.status_code == 404


# ── SSE stream ───────────────────────────────────────────────────────────────

def test_events_stream_emits_existing_events(client, conn_factory):
    """The SSE endpoint replays events with id > since_id, then keeps polling."""
    import asyncio

    from agent_interface.web.app import _event_stream

    client.post("/projects", json={"name": "p1"})
    tid = client.post("/tasks", json={"project": "p1", "title": "t"}).json()["id"]
    client.patch(f"/tasks/{tid}", json={"priority": 0})

    class _FakeRequest:
        def __init__(self, disconnect_after: int) -> None:
            self._left = disconnect_after

        async def is_disconnected(self) -> bool:
            self._left -= 1
            return self._left <= 0

    async def _collect() -> list[str]:
        out: list[str] = []
        gen = _event_stream(
            _FakeRequest(disconnect_after=3),
            conn_factory,
            since_id=0,
            poll=0.01,
        )
        async for chunk in gen:
            out.append(chunk)
        return out

    chunks = asyncio.run(_collect())
    data_frames = [c for c in chunks if c.startswith("id:")]
    assert len(data_frames) >= 2

    parsed = []
    for frame in data_frames:
        for line in frame.splitlines():
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: "):]))
    types = [e["event_type"] for e in parsed]
    assert "created" in types
    assert "updated" in types


def test_events_stream_route_registered(client):
    """The SSE endpoint shows up in the OpenAPI schema."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/events/stream" in paths
    assert "get" in paths["/events/stream"]


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_cors_allows_cross_origin(client):
    r = client.options(
        "/projects",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
