"""FastAPI app exposing the orchestrator core over HTTP.

Routes:
  GET    /projects                 — list projects
  POST   /projects                 — create a project
  GET    /projects/{id}/tasks      — list tasks in a project
  POST   /tasks                    — create a task
  PATCH  /tasks/{id}               — update status / priority / assignment
  DELETE /tasks/{id}               — hard-delete a task
  GET    /tasks/{id}/events        — event log for a task
  GET    /events/stream            — SSE stream of new task events

CORS is wide-open for local dev. Callers instantiate the app via
`create_app()`; `agi serve` runs it through uvicorn.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import AsyncIterator, Iterable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent_interface.orchestrator import core
from agent_interface.orchestrator.db import get_connection
from agent_interface.orchestrator.models import Task, TaskEvent
from agent_interface.orchestrator.states import TaskStatus
from agent_interface.web.schemas import (
    ProjectCreate,
    ProjectOut,
    TaskCreate,
    TaskEventOut,
    TaskOut,
    TaskPatch,
)

# ── DB dependency ────────────────────────────────────────────────────────────

ConnFactory = "callable[[], sqlite3.Connection]"


def _default_conn_factory() -> sqlite3.Connection:
    return get_connection()


def create_app(conn_factory=_default_conn_factory) -> FastAPI:
    """Build the FastAPI app.

    `conn_factory` is injected so tests can swap in an isolated DB. Each
    request opens a fresh connection and closes it on teardown — sqlite3
    connections aren't safe to share across threads.
    """
    app = FastAPI(title="agi orchestrator", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_db():
        conn = conn_factory()
        try:
            yield conn
        finally:
            conn.close()

    # ── projects ─────────────────────────────────────────────────────────────

    @app.get("/projects", response_model=list[ProjectOut])
    def list_projects(
        include_archived: bool = False,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> list[ProjectOut]:
        projects = core.list_projects(conn, include_archived=include_archived)
        return [ProjectOut(**p.__dict__) for p in projects]

    @app.post("/projects", response_model=ProjectOut, status_code=201)
    def create_project(
        body: ProjectCreate,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> ProjectOut:
        try:
            project = core.create_project(
                conn, body.name,
                description=body.description,
                autonomy=body.autonomy,
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ProjectOut(**project.__dict__)

    @app.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
    def list_project_tasks(
        project_id: str,
        status: Optional[str] = None,
        include_closed: bool = False,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> list[TaskOut]:
        if core.get_project(conn, project_id) is None:
            raise HTTPException(status_code=404, detail=f"No such project: {project_id}")
        if status is not None:
            try:
                TaskStatus(status)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from e
        tasks = core.list_tasks(
            conn,
            project=project_id,
            status=status,
            include_closed=include_closed,
        )
        return [_task_to_out(t) for t in tasks]

    # ── tasks ────────────────────────────────────────────────────────────────

    @app.post("/tasks", response_model=TaskOut, status_code=201)
    def create_task(
        body: TaskCreate,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> TaskOut:
        try:
            task = core.add_task(
                conn,
                body.project,
                body.title,
                description=body.description,
                priority=body.priority,
                tags=body.tags,
                depends_on=body.depends_on,
                parent_id=body.parent_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _task_to_out(task)

    @app.patch("/tasks/{task_id}", response_model=TaskOut)
    def patch_task(
        task_id: str,
        body: TaskPatch,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> TaskOut:
        task = core.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"No such task: {task_id}")

        if body.status is not None:
            try:
                TaskStatus(body.status)
            except ValueError as e:
                raise HTTPException(
                    status_code=400, detail=f"Invalid status: {body.status}",
                ) from e
            task = _apply_status(conn, task, body)

        if (
            body.priority is not None
            or body.assigned_session_id is not None
            or body.clear_assignment
        ):
            try:
                task = core.update_task_fields(
                    conn,
                    task_id,
                    priority=body.priority,
                    assigned_session_id=body.assigned_session_id,
                    clear_assignment=body.clear_assignment,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

        return _task_to_out(task)

    @app.delete("/tasks/{task_id}", status_code=204)
    def delete_task(
        task_id: str,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> None:
        try:
            deleted = core.delete_task(conn, task_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No such task: {task_id}")

    @app.get("/tasks/{task_id}/events", response_model=list[TaskEventOut])
    def list_task_events(
        task_id: str,
        conn: sqlite3.Connection = Depends(get_db),
    ) -> list[TaskEventOut]:
        if core.get_task(conn, task_id) is None:
            raise HTTPException(status_code=404, detail=f"No such task: {task_id}")
        events = core.list_events(conn, task_id)
        return [_event_to_out(e) for e in events]

    # ── event stream (SSE) ───────────────────────────────────────────────────

    @app.get("/events/stream")
    async def events_stream(
        request: Request,
        since_id: int = 0,
        poll_seconds: float = 1.0,
    ) -> StreamingResponse:
        return StreamingResponse(
            _event_stream(request, conn_factory, since_id=since_id, poll=poll_seconds),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


# ── serialization helpers ────────────────────────────────────────────────────

def _task_to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        status=task.status.value if hasattr(task.status, "value") else task.status,
        description=task.description,
        priority=task.priority,
        tags=list(task.tags),
        parent_id=task.parent_id,
        creator=task.creator,
        spawned_from_task=task.spawned_from_task,
        spawned_from_session=task.spawned_from_session,
        assigned_session_id=task.assigned_session_id,
        worktree_path=task.worktree_path,
        depends_on=list(task.depends_on),
        created_at=task.created_at,
        updated_at=task.updated_at,
        closed_at=task.closed_at,
    )


def _event_to_out(event: TaskEvent) -> TaskEventOut:
    return TaskEventOut(
        id=event.id,
        task_id=event.task_id,
        event_type=event.event_type,
        actor=event.actor,
        payload_json=event.payload_json,
        created_at=event.created_at,
    )


# ── status transition mapping ────────────────────────────────────────────────

def _apply_status(
    conn: sqlite3.Connection,
    task: Task,
    body: TaskPatch,
) -> Task:
    """Map a target status onto the right core transition helper."""
    target = body.status
    assert target is not None

    current = task.status.value if hasattr(task.status, "value") else task.status
    if current == target:
        return task

    try:
        if target == TaskStatus.READY.value:
            if current == TaskStatus.BACKLOG.value:
                return core.promote(conn, task.id)
            if current == TaskStatus.BLOCKED.value:
                return core.unblock_task(conn, task.id)
        if target == TaskStatus.BLOCKED.value:
            reason = body.block_reason or "blocked via API"
            needs = body.block_needs or "user"
            return core.block_task(conn, task.id, reason, needs=needs)
        if target == TaskStatus.DONE.value:
            summary = body.done_summary or "closed via API"
            return core.done_task(conn, task.id, summary)
        if target == TaskStatus.IN_PROGRESS.value and current == TaskStatus.DONE.value:
            return core.reopen_task(conn, task.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Fallback: direct status write for transitions that don't map to a
    # dedicated helper (e.g. in_progress ↔ review, manual backlog move).
    core._set_status(conn, task.id, target)
    core._append_event(conn, task.id, "status_changed", payload={"to": target})
    conn.commit()
    updated = core.get_task(conn, task.id)
    assert updated is not None
    return updated


# ── SSE producer ─────────────────────────────────────────────────────────────

def _fetch_events_after(conn: sqlite3.Connection, last_id: int) -> Iterable[TaskEvent]:
    rows = conn.execute(
        "SELECT * FROM task_events WHERE id > ? ORDER BY id ASC",
        (last_id,),
    ).fetchall()
    for r in rows:
        yield TaskEvent(
            id=r["id"],
            task_id=r["task_id"],
            event_type=r["event_type"],
            actor=r["actor"],
            payload_json=r["payload_json"],
            created_at=r["created_at"],
        )


def _format_sse(event: TaskEvent) -> str:
    payload = {
        "id": event.id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "payload": event.payload_json,
        "created_at": event.created_at,
    }
    return f"id: {event.id}\ndata: {json.dumps(payload)}\n\n"


async def _event_stream(
    request: Request,
    conn_factory,
    *,
    since_id: int,
    poll: float,
) -> AsyncIterator[str]:
    # Emit a prelude so intermediaries flush the connection and the client
    # confirms the stream is live before the first real event.
    yield ": connected\n\n"

    last_id = max(since_id, 0)
    # Heartbeat every ~15s so idle connections don't get reaped by proxies.
    heartbeat_every = max(int(15 / max(poll, 0.01)), 1)
    ticks = 0

    while True:
        if await request.is_disconnected():
            return

        conn = conn_factory()
        try:
            events = list(_fetch_events_after(conn, last_id))
        finally:
            conn.close()

        for event in events:
            yield _format_sse(event)
            if event.id is not None:
                last_id = max(last_id, event.id)

        ticks += 1
        if ticks % heartbeat_every == 0:
            yield ": keepalive\n\n"

        await asyncio.sleep(poll)
