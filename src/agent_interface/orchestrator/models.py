"""Data models for projects, tasks, and task events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Project:
    id: str
    name: str
    description: Optional[str] = None
    autonomy: str = "none"
    created_at: str = field(default_factory=_now_utc)
    updated_at: str = field(default_factory=_now_utc)
    archived_at: Optional[str] = None


@dataclass
class Task:
    id: str
    project_id: str
    title: str
    status: str = "backlog"
    description: Optional[str] = None
    priority: int = 2
    tags: list[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    creator: str = "user"
    spawned_from_task: Optional[str] = None
    spawned_from_session: Optional[str] = None
    assigned_session_id: Optional[str] = None
    worktree_path: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_utc)
    updated_at: str = field(default_factory=_now_utc)
    closed_at: Optional[str] = None


@dataclass
class TaskEvent:
    id: Optional[int]
    task_id: str
    event_type: str
    actor: str = "user"
    payload_json: Optional[str] = None
    created_at: str = field(default_factory=_now_utc)
