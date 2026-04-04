"""Tests for process scanning."""

from agent_interface.scan import (
    ProcessInfo,
    _looks_like_agent,
    deduplicate_by_pane,
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


def _tmux_proc(pid, cwd="/tmp", session="s", window="0", pane="0"):
    return ProcessInfo(
        pid=pid, cwd=cwd, cmdline="claude",
        tmux_session=session, tmux_window=window, tmux_pane=pane,
    )


def test_deduplicate_keeps_lowest_pid_per_pane():
    procs = [_tmux_proc(100, pane="1"), _tmux_proc(200, pane="1")]
    result = deduplicate_by_pane(procs)
    assert len(result) == 1
    assert result[0].pid == 100


def test_deduplicate_keeps_different_panes():
    procs = [_tmux_proc(100, cwd="/a", pane="0"), _tmux_proc(200, cwd="/b", pane="1")]
    result = deduplicate_by_pane(procs)
    assert len(result) == 2


def test_deduplicate_keeps_non_tmux():
    procs = [
        ProcessInfo(pid=100, cwd="/a", cmdline="claude"),
        ProcessInfo(pid=200, cwd="/b", cmdline="claude"),
    ]
    result = deduplicate_by_pane(procs)
    assert len(result) == 2
