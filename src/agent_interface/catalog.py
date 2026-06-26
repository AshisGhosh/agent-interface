"""Agent-facing tool catalog — how agents discover what `agi` can do.

Features are useless if agents don't know they exist. The optimizer keeps
shipping new `agi` commands, but nothing told agents in other projects about
them, so they went unused. This enumerates the *cross-project utility* commands
straight from the live CLI (so newly-shipped features appear automatically) and
renders a compact catalog that gets injected into every agent's CLAUDE.md.
"""

from __future__ import annotations

# Internal / admin / orchestration / session-nav commands — not general
# project tools an agent reaches for while working in some other repo.
_HIDE_COMMANDS = {
    "hook", "mcp", "serve", "bot", "bot-stop", "dashboard", "init-hooks",
    "scan", "doctor", "heartbeat", "register", "update-state", "notify-test",
    "watch", "board", "dispatch", "approve", "reject", "review", "features",
    "insights", "prune", "restore", "archive", "rename", "next", "done",
    "progress", "block", "unblock", "label", "list", "show", "waiting", "jump",
}
_HIDE_GROUPS = {"optimize", "supervisor", "projects", "tasks", "usage"}


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n")[0].strip()


def _cmd_help(cmd) -> str:
    if getattr(cmd, "help", None):
        return _first_line(cmd.help)
    cb = getattr(cmd, "callback", None)
    return _first_line(cb.__doc__) if cb else ""


def agent_tools() -> list[tuple[str, str]]:
    """(name, one-line help) for cross-project agi tools, from the live CLI.

    Plural twins (runs/notes/jobs/…) are collapsed into their singular verb.
    """
    from agent_interface.cli import app

    names = {c.name for c in app.registered_commands if c.name}
    out: list[tuple[str, str]] = []

    for cmd in app.registered_commands:
        name = cmd.name
        if not name or name in _HIDE_COMMANDS:
            continue
        # Collapse a plural twin when its singular also exists (runs→run).
        if name.endswith("s") and name[:-1] in names:
            continue
        out.append((name, _cmd_help(cmd)))

    for grp in app.registered_groups:
        if not grp.name or grp.name in _HIDE_GROUPS:
            continue
        help_ = _first_line(getattr(grp.typer_instance.info, "help", "") or "")
        out.append((grp.name, help_))

    return sorted(out)


def render_markdown() -> str:
    """Compact catalog for embedding in the agent CLAUDE.md instruction."""
    lines = [
        "### Cross-project agi tools",
        "These work from ANY project directory. Run `agi <cmd> -h` for usage, "
        "or `agi commands` to list them.",
    ]
    for name, help_ in agent_tools():
        lines.append(f"- `agi {name}` — {help_}" if help_ else f"- `agi {name}`")
    return "\n".join(lines)
