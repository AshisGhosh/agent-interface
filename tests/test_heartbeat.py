"""Tests for heartbeat efficiency: hook reinstall is throttled, not per-tick."""

import agent_interface.scan as scan_mod
from agent_interface.cli import _hooks_install_due


def test_scan_skips_hook_install_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(scan_mod, "find_claude_processes", lambda: [])

    called = {"n": 0}

    def boom():
        called["n"] += 1
        return (True, "installed")

    # If scan tried to install hooks, this import target would run.
    import agent_interface.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "install_hooks", boom)

    installed, results = scan_mod.scan_and_register(install_hooks=False)
    assert installed is False
    assert called["n"] == 0  # never touched the expensive installer


def test_scan_installs_hooks_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AGI_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(scan_mod, "find_claude_processes", lambda: [])

    import agent_interface.hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "install_hooks", lambda: (True, "installed"))

    installed, _ = scan_mod.scan_and_register(install_hooks=True)
    assert installed is True


def test_hooks_install_throttle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    # First call in a fresh window → due (and records the stamp).
    assert _hooks_install_due(interval_seconds=3600) is True
    # Immediately after → throttled.
    assert _hooks_install_due(interval_seconds=3600) is False
    # With a zero window, it's always due again.
    assert _hooks_install_due(interval_seconds=0) is True
