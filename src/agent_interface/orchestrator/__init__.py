"""Orchestrator — task management layer on top of the session registry.

Designed as a self-contained subpackage so it can be extracted later.
Cross-boundary reads go through `agent_interface.registry`; nothing in this
package should import private session internals.
"""

from agent_interface.orchestrator.core import (
    add_task,
    block_task,
    claim_next,
    create_project,
    done_task,
    get_task,
    list_events,
    list_projects,
    list_tasks,
    progress,
    promote,
    reopen_task,
    unblock_task,
)
from agent_interface.orchestrator.models import Project, Task, TaskEvent
from agent_interface.orchestrator.states import TaskStatus

__all__ = [
    "Project",
    "Task",
    "TaskEvent",
    "TaskStatus",
    "add_task",
    "block_task",
    "claim_next",
    "create_project",
    "done_task",
    "get_task",
    "list_events",
    "list_projects",
    "list_tasks",
    "progress",
    "promote",
    "reopen_task",
    "unblock_task",
]
