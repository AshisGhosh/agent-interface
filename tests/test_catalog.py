"""Tests for the agent-facing tool catalog (feature discovery)."""

from agent_interface.catalog import agent_tools, render_markdown


def test_catalog_includes_shipped_features():
    names = {n for n, _ in agent_tools()}
    # The cross-project tools the optimizer shipped must be discoverable.
    for tool in ("run", "note", "job", "flake", "assess", "up", "down", "dash"):
        assert tool in names, f"{tool} missing from agent catalog"


def test_catalog_hides_internal_commands():
    names = {n for n, _ in agent_tools()}
    for internal in ("hook", "mcp", "heartbeat", "doctor", "serve", "dispatch"):
        assert internal not in names


def test_catalog_collapses_plural_twins():
    names = {n for n, _ in agent_tools()}
    # singular kept, plural twin dropped
    assert "run" in names and "runs" not in names
    assert "note" in names and "notes" not in names


def test_catalog_entries_have_help():
    tools = dict(agent_tools())
    # A representative tool should carry a one-line description.
    assert tools["flake"]
    assert len(tools["flake"]) > 3


def test_render_markdown_is_embeddable():
    md = render_markdown()
    assert "Cross-project agi tools" in md
    assert "`agi flake`" in md
    assert "agi commands" in md  # points agents at the live list
