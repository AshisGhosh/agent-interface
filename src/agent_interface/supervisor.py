"""Supervision — keep agi up by design, across crashes and reboots.

Installs two systemd *user* units:

  - ``agi-bot.service``    — the Telegram bot, ``Restart=always`` so a crash is
                             recovered in seconds.
  - ``agi-heartbeat.timer`` → ``agi-heartbeat.service`` — runs ``agi heartbeat``
                             on a fixed cadence: reconcile the registry, keep the
                             bot + dashboard alive, and tick the optimizer. This
                             is a second, independent recovery path — even if the
                             bot service itself were disabled, the heartbeat
                             revives it.

With user-lingering enabled the units start at boot and survive logout, so the
control plane is up whenever the machine is.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

BOT_SERVICE = "agi-bot.service"
HEARTBEAT_SERVICE = "agi-heartbeat.service"
HEARTBEAT_TIMER = "agi-heartbeat.timer"

HEARTBEAT_INTERVAL_SEC = 120

# systemd user services start with a minimal PATH; the bot/heartbeat need to
# find claude, tmux, git (e.g. for autonomous dispatch). ~/.local/bin first.
_SERVICE_PATH = "%h/.local/bin:/usr/local/bin:/usr/bin:/bin"


def _agi_path() -> str:
    """Resolve the agi binary, preferring the global install over a venv shim."""
    for candidate in (
        str(Path.home() / ".local" / "bin" / "agi"),
        shutil.which("agi") or "",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return "agi"


def render_units(agi: str | None = None) -> dict[str, str]:
    """Render the unit files as {filename: contents}. Pure — easy to test."""
    agi = agi or _agi_path()
    return {
        BOT_SERVICE: (
            "[Unit]\n"
            "Description=agi Telegram bot (coding-agent control plane)\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={agi} bot --fg\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            f"Environment=PATH={_SERVICE_PATH}\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        ),
        HEARTBEAT_SERVICE: (
            "[Unit]\n"
            "Description=agi self-heal heartbeat (reconcile + keep bot alive)\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"Environment=PATH={_SERVICE_PATH}\n"
            f"ExecStart={agi} heartbeat\n"
        ),
        HEARTBEAT_TIMER: (
            "[Unit]\n"
            "Description=Run agi heartbeat periodically\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=30s\n"
            f"OnUnitActiveSec={HEARTBEAT_INTERVAL_SEC}s\n"
            "AccuracySec=15s\n"
            "Persistent=true\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=30, check=check,
    )


def _has_systemd_user() -> bool:
    return shutil.which("systemctl") is not None


def install() -> tuple[bool, list[str]]:
    """Write units, enable lingering, and start everything. Returns (ok, log)."""
    log: list[str] = []
    if not _has_systemd_user():
        return False, ["systemctl not found — use `agi heartbeat` via cron instead."]

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, contents in render_units().items():
        (UNIT_DIR / name).write_text(contents)
        log.append(f"wrote {name}")

    # Survive logout + reboot. Best-effort: lingering may already be on, or
    # require interactive auth on some systems.
    linger = subprocess.run(
        ["loginctl", "enable-linger"], capture_output=True, text=True, timeout=15,
    )
    log.append("lingering enabled" if linger.returncode == 0
               else f"linger: {linger.stderr.strip() or 'failed (enable manually)'}")

    _systemctl("daemon-reload")
    for unit in (BOT_SERVICE, HEARTBEAT_TIMER):
        r = _systemctl("enable", "--now", unit)
        log.append(f"{unit}: {'started' if r.returncode == 0 else r.stderr.strip()}")

    return True, log


def uninstall() -> tuple[bool, list[str]]:
    log: list[str] = []
    if not _has_systemd_user():
        return False, ["systemctl not found."]
    for unit in (HEARTBEAT_TIMER, HEARTBEAT_SERVICE, BOT_SERVICE):
        _systemctl("disable", "--now", unit)
        f = UNIT_DIR / unit
        if f.exists():
            f.unlink()
            log.append(f"removed {unit}")
    _systemctl("daemon-reload")
    return True, log


def status() -> dict[str, str]:
    """Active/inactive state of each managed unit."""
    out: dict[str, str] = {}
    if not _has_systemd_user():
        return {"systemd": "unavailable"}
    for unit in (BOT_SERVICE, HEARTBEAT_TIMER):
        r = _systemctl("is-active", unit)
        out[unit] = r.stdout.strip() or "unknown"
    return out
