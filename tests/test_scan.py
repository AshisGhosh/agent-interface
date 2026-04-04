"""Tests for process scanning."""

from agent_interface.scan import (
    ProcessInfo,
    _looks_like_agent,
)


def test_looks_like_agent_claude_binary():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="claude --model opus")
    assert _looks_like_agent(info)


def test_looks_like_agent_node_claude():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="node /usr/lib/claude-code/cli.js")
    assert _looks_like_agent(info)


def test_rejects_grep():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="grep claude somefile")
    assert not _looks_like_agent(info)


def test_rejects_pgrep():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="pgrep -f claude")
    assert not _looks_like_agent(info)


def test_rejects_agi_command():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="python -m agent_interface.cli scan")
    assert not _looks_like_agent(info)


def test_rejects_agi_direct():
    info = ProcessInfo(pid=1, cwd="/tmp", cmdline="agi scan")
    assert not _looks_like_agent(info)


def test_rejects_claude_internal_processes():
    info = ProcessInfo(pid=1, cwd="/home/user/.claude/usage-data", cmdline="claude something")
    assert not _looks_like_agent(info)
