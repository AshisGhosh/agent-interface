"""Tests for process scanning."""

import agent_interface.scan as scan_mod
from agent_interface.scan import (
    ProcessInfo,
    _looks_like_agent,
    _pid_identity,
    deduplicate_by_pane,
    resolve_agent_pid,
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


# ── pid identity ──────────────────────────────────────────────────────────────


def test_pid_identity_agent(monkeypatch):
    monkeypatch.setattr(scan_mod, "_proc_cmdline", lambda pid: "claude --model opus")
    assert _pid_identity(1) is True


def test_pid_identity_not_agent(monkeypatch):
    monkeypatch.setattr(scan_mod, "_proc_cmdline", lambda pid: "vim notes.md")
    assert _pid_identity(1) is False


def test_pid_identity_rejects_agi_itself(monkeypatch):
    monkeypatch.setattr(scan_mod, "_proc_cmdline", lambda pid: "agi hook")
    assert _pid_identity(1) is False


def test_pid_identity_unknown_when_no_proc(monkeypatch):
    monkeypatch.setattr(scan_mod, "_proc_cmdline", lambda pid: None)
    assert _pid_identity(1) is None


# ── resolve_agent_pid (anchor walk) ───────────────────────────────────────────


def test_resolve_agent_pid_walks_past_shell(monkeypatch):
    """Hook(101) → shell(101's parent=200) → claude(300). Resolves to 300."""
    parents = {101: 200, 200: 300, 300: 1}
    identities = {101: False, 200: False, 300: True}
    monkeypatch.setattr(scan_mod, "_get_parent_pid", lambda pid: parents.get(pid))
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: identities.get(pid))

    assert resolve_agent_pid(101) == 300


def test_resolve_agent_pid_immediate(monkeypatch):
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: True)
    assert resolve_agent_pid(555) == 555


def test_resolve_agent_pid_none_when_no_agent(monkeypatch):
    parents = {101: 200, 200: 1}
    monkeypatch.setattr(scan_mod, "_get_parent_pid", lambda pid: parents.get(pid))
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: False)
    assert resolve_agent_pid(101) is None


def test_resolve_agent_pid_handles_cycle(monkeypatch):
    """A pathological parent cycle must not hang."""
    parents = {101: 200, 200: 101}
    monkeypatch.setattr(scan_mod, "_get_parent_pid", lambda pid: parents.get(pid))
    monkeypatch.setattr(scan_mod, "_pid_identity", lambda pid: False)
    assert resolve_agent_pid(101) is None
