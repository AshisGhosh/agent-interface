"""Shared test fixtures."""

import pytest

from agent_interface.db import get_connection


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema initialized."""
    c = get_connection(db_path=None)
    # Override to use in-memory DB.
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    # Apply schema.
    from agent_interface.db import _SCHEMA_SQL
    c.executescript(_SCHEMA_SQL)
    return c
