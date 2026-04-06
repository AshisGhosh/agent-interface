"""Data models for sessions and events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Session:
    id: str
    state: str = "unknown"
    label: Optional[str] = None
    host: Optional[str] = None
    cwd: Optional[str] = None
    repo_root: Optional[str] = None
    branch: Optional[str] = None
    tmux_session: Optional[str] = None
    tmux_window: Optional[str] = None
    tmux_pane: Optional[str] = None
    worktree_path: Optional[str] = None
    pid: Optional[int] = None
    is_managed: bool = False
    summary: Optional[str] = None
    last_tool: Optional[str] = None
    tool_count: int = 0
    created_at: str = field(default_factory=_now_utc)
    updated_at: str = field(default_factory=_now_utc)
    last_seen_at: str = field(default_factory=_now_utc)
    archived_at: Optional[str] = None


@dataclass
class Event:
    id: Optional[int]
    session_id: str
    event_type: str
    payload_json: Optional[str] = None
    created_at: str = field(default_factory=_now_utc)
