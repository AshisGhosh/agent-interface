"""Task status definitions."""

from enum import Enum


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"


# Statuses eligible to be claimed by an agent.
CLAIMABLE = {TaskStatus.READY}

# Statuses considered "open" (not yet closed).
OPEN_STATUSES = {
    TaskStatus.BACKLOG,
    TaskStatus.READY,
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.BLOCKED,
}


class Autonomy(str, Enum):
    NONE = "none"
    SUBTASKS_ONLY = "subtasks_only"
    FULL = "full"
