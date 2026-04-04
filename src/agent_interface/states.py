"""Session state definitions."""

from enum import Enum


class SessionState(str, Enum):
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    TESTS_FAILED = "tests_failed"
    DONE = "done"
    IDLE = "idle"
    ARCHIVED = "archived"
    STALE = "stale"
    UNKNOWN = "unknown"


# States that mean the session is "active" for listing purposes.
ACTIVE_STATES = {
    SessionState.RUNNING,
    SessionState.WAITING_FOR_USER,
    SessionState.BLOCKED,
    SessionState.TESTS_FAILED,
    SessionState.IDLE,
    SessionState.UNKNOWN,
}

# Stale threshold in seconds (30 minutes).
STALE_THRESHOLD_SECONDS = 30 * 60
