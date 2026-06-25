"""Tests for the per-project notebook (agi note / agi notes)."""

import sqlite3

import pytest

from agent_interface.notes import (
    add_note,
    list_notes,
    project_key,
    remove_note,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── add / list ─────────────────────────────────────────────────────────────────


def test_add_and_list(conn):
    add_note(conn, project="/p", note="build needs node 18", created_at=100.0)
    add_note(conn, project="/p", note="tests are flaky, retry", created_at=200.0)
    notes = list_notes(conn, "/p")
    assert [n["note"] for n in notes] == [
        "tests are flaky, retry",
        "build needs node 18",
    ]  # newest first


def test_notes_are_project_scoped(conn):
    add_note(conn, project="/a", note="alpha", created_at=1.0)
    add_note(conn, project="/b", note="beta", created_at=1.0)
    assert [n["note"] for n in list_notes(conn, "/a")] == ["alpha"]
    assert [n["note"] for n in list_notes(conn, "/b")] == ["beta"]


def test_tag_filter(conn):
    add_note(conn, project="/p", note="generic", created_at=1.0)
    add_note(conn, project="/p", note="ci hint", tag="ci", created_at=2.0)
    tagged = list_notes(conn, "/p", tag="ci")
    assert [n["note"] for n in tagged] == ["ci hint"]
    assert tagged[0]["tag"] == "ci"


def test_search_filter(conn):
    add_note(conn, project="/p", note="the BUILD command is make", created_at=1.0)
    add_note(conn, project="/p", note="deploy with fly", created_at=2.0)
    # case-insensitive substring match
    found = list_notes(conn, "/p", query="build")
    assert [n["note"] for n in found] == ["the BUILD command is make"]


def test_limit(conn):
    for i in range(5):
        add_note(conn, project="/p", note=f"n{i}", created_at=float(i))
    assert len(list_notes(conn, "/p", limit=3)) == 3


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove(conn):
    nid = add_note(conn, project="/p", note="temp", created_at=1.0)
    assert remove_note(conn, "/p", nid) is True
    assert list_notes(conn, "/p") == []
    # gone now → removing again is a no-op
    assert remove_note(conn, "/p", nid) is False


def test_remove_is_project_scoped(conn):
    nid = add_note(conn, project="/a", note="keep", created_at=1.0)
    # an id from /a cannot be removed via project /b
    assert remove_note(conn, "/b", nid) is False
    assert [n["note"] for n in list_notes(conn, "/a")] == ["keep"]


# ── project key ─────────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
