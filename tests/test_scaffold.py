"""Tests for the reusable code-scaffold library (agi scaffold)."""

import sqlite3

import pytest

from agent_interface.scaffold import (
    GLOBAL_SCOPE,
    get_scaffold,
    list_scaffolds,
    parse_var,
    placeholders,
    project_key,
    remove_scaffold,
    render,
    save_scaffold,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── save / get ───────────────────────────────────────────────────────────────


def test_save_and_get_global(conn):
    _id, created = save_scaffold(conn, name="comp", body="hello", now=1.0)
    assert created is True
    row = get_scaffold(conn, "comp")
    assert row["body"] == "hello"
    assert row["scope"] == GLOBAL_SCOPE


def test_resave_overwrites_and_keeps_created_at(conn):
    id1, created1 = save_scaffold(conn, name="comp", body="v1", now=1.0)
    id2, created2 = save_scaffold(
        conn, name="comp", body="v2", description="d", now=2.0
    )
    assert created1 is True and created2 is False
    assert id1 == id2  # same row, updated in place
    row = get_scaffold(conn, "comp")
    assert row["body"] == "v2"
    assert row["description"] == "d"
    assert row["created_at"] == 1.0  # preserved
    assert row["updated_at"] == 2.0


def test_get_missing_returns_none(conn):
    assert get_scaffold(conn, "nope") is None


# ── scope resolution ─────────────────────────────────────────────────────────


def test_project_scope_shadows_global(conn):
    save_scaffold(conn, name="comp", body="global-body", now=1.0)
    save_scaffold(conn, name="comp", body="proj-body", scope="/p", now=2.0)
    # With a project, the project-scoped one wins.
    assert get_scaffold(conn, "comp", project="/p")["body"] == "proj-body"
    # A different project sees only the global.
    assert get_scaffold(conn, "comp", project="/other")["body"] == "global-body"
    # No project context falls back to global.
    assert get_scaffold(conn, "comp")["body"] == "global-body"


def test_same_name_distinct_scopes_coexist(conn):
    save_scaffold(conn, name="comp", body="g", now=1.0)
    save_scaffold(conn, name="comp", body="p", scope="/p", now=1.0)
    rows = conn.execute("SELECT COUNT(*) FROM scaffolds").fetchone()
    assert rows[0] == 2  # UNIQUE(scope, name) allows both


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_shows_globals_and_project_only(conn):
    save_scaffold(conn, name="g1", body="x", now=1.0)
    save_scaffold(conn, name="p1", body="x", scope="/p", now=1.0)
    save_scaffold(conn, name="other", body="x", scope="/other", now=1.0)
    names = {r["name"] for r in list_scaffolds(conn, project="/p")}
    assert names == {"g1", "p1"}  # not /other's
    assert {r["name"] for r in list_scaffolds(conn)} == {"g1"}  # globals only


def test_list_sorts_project_before_global_on_name_tie(conn):
    save_scaffold(conn, name="comp", body="g", now=1.0)
    save_scaffold(conn, name="comp", body="p", scope="/p", now=1.0)
    rows = list_scaffolds(conn, project="/p")
    assert [r["scope"] for r in rows] == ["/p", GLOBAL_SCOPE]


# ── remove ───────────────────────────────────────────────────────────────────


def test_remove_is_scope_specific(conn):
    save_scaffold(conn, name="comp", body="g", now=1.0)
    save_scaffold(conn, name="comp", body="p", scope="/p", now=1.0)
    assert remove_scaffold(conn, "comp", scope="/p") is True
    assert get_scaffold(conn, "comp", project="/p")["body"] == "g"  # global survives
    assert remove_scaffold(conn, "comp", scope="/p") is False  # gone now


# ── placeholders / render ─────────────────────────────────────────────────────


def test_placeholders_distinct_in_order(conn):
    body = "class {{name}} extends {{base}} {{ name }}"
    assert placeholders(body) == ["name", "base"]


def test_placeholders_none(conn):
    assert placeholders("plain text") == []


def test_render_fills_and_reports_missing():
    body = "<{{tag}} class='{{cls}}'>{{tag}}</{{tag}}>"
    out, missing = render(body, {"tag": "SpellBar"})
    assert out == "<SpellBar class='{{cls}}'>SpellBar</SpellBar>"
    assert missing == ["cls"]  # unfilled hole left verbatim, reported once


def test_render_whitespace_tolerant():
    out, missing = render("{{ name }}", {"name": "X"})
    assert out == "X"
    assert missing == []


def test_render_no_placeholders_is_identity():
    out, missing = render("nothing here", {"x": "y"})
    assert out == "nothing here"
    assert missing == []


# ── parse_var ────────────────────────────────────────────────────────────────


def test_parse_var_basic():
    assert parse_var("name=Button") == ("name", "Button")


def test_parse_var_value_may_contain_equals():
    assert parse_var("expr=a==b") == ("expr", "a==b")


def test_parse_var_strips_key():
    assert parse_var("  name = Button") == ("name", " Button")


@pytest.mark.parametrize("bad", ["noequals", "=value", "  =x"])
def test_parse_var_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_var(bad)


# ── project key ──────────────────────────────────────────────────────────────


def test_project_key_falls_back_to_cwd(tmp_path):
    assert project_key(str(tmp_path)) == str(tmp_path.resolve())
