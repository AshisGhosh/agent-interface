"""Pydantic request/response models for the HTTP surface."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    autonomy: str = "none"


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    autonomy: str
    created_at: str
    updated_at: str
    archived_at: Optional[str] = None


class TaskCreate(BaseModel):
    project: str = Field(..., description="Project id or name.")
    title: str
    description: Optional[str] = None
    priority: int = 2
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None


class TaskPatch(BaseModel):
    status: Optional[str] = None
    priority: Optional[int] = None
    assigned_session_id: Optional[str] = None
    clear_assignment: bool = False
    block_reason: Optional[str] = None
    block_needs: Optional[str] = None
    done_summary: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    status: str
    description: Optional[str] = None
    priority: int
    tags: list[str]
    parent_id: Optional[str] = None
    creator: str
    spawned_from_task: Optional[str] = None
    spawned_from_session: Optional[str] = None
    assigned_session_id: Optional[str] = None
    worktree_path: Optional[str] = None
    depends_on: list[str]
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None


class TaskEventOut(BaseModel):
    id: Optional[int]
    task_id: str
    event_type: str
    actor: str
    payload_json: Optional[str] = None
    created_at: str


class DispatchRequest(BaseModel):
    project: str = Field(..., description="Project id or name.")
    n: int = Field(1, ge=1, le=20, description="Number of agents to spawn.")
    worktree: bool = True
    cwd: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class DispatchResultOut(BaseModel):
    task_id: str
    session_id: str
    tmux_target: str
    worktree_path: Optional[str] = None


class DispatchResponse(BaseModel):
    dispatched: int
    agents: list[DispatchResultOut]
