"""Shared test fixtures."""

import sqlite3

import pytest

from agent_interface.db import _SCHEMA_SQL, _migrate


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(_SCHEMA_SQL)
    _migrate(c)
    return c
