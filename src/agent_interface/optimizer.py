"""Autonomous self-improvement loop.

On its own cadence (driven by the heartbeat), this mines the session registry
for recurring workflows (see :mod:`agent_interface.insights`) and dispatches a
coding agent to design a reusable automation for the top opportunity.

This *spends tokens and takes actions unattended*, so it is wrapped in hard
guardrails, all independently testable:

  - an explicit ``enabled`` flag (off until turned on),
  - a kill-switch file that hard-stops the loop the moment it appears,
  - a per-day dispatch cap, and
  - a minimum interval between dispatches.

The decision logic is pure functions; :func:`maybe_run` is the only effectful
entry point and never raises (it is called from the best-effort heartbeat).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agent_interface.insights import WorkflowOpportunity, analyze_sessions

STATE_PATH = Path.home() / ".config" / "agi" / "optimizer.json"
KILLSWITCH_PATH = Path.home() / ".config" / "agi" / "optimizer.disabled"
AUDIT_PATH = Path.home() / ".local" / "share" / "agent-interface" / "optimizer-audit.log"

DEFAULTS = {
    "enabled": False,
    "max_dispatches_per_day": 2,
    "min_interval_seconds": 6 * 60 * 60,  # 6h
    "project_name": "agi-self-improve",
    "dispatch_cwd": None,  # defaults to the agent-interface repo at runtime
}


@dataclass
class Decision:
    ok: bool
    reason: str


# ── config + state ────────────────────────────────────────────────────────────


def _config() -> dict[str, Any]:
    from agent_interface.telegram import _load_config

    cfg = dict(DEFAULTS)
    cfg.update(_load_config().get("optimizer", {}) or {})
    return cfg


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"day": "", "dispatches_today": 0, "last_run": 0.0, "acted_repos": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"day": "", "dispatches_today": 0, "last_run": 0.0, "acted_repos": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _day_str(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def roll_over_day(state: dict[str, Any], now: float) -> dict[str, Any]:
    """Reset the daily counter when the UTC day changes."""
    today = _day_str(now)
    if state.get("day") != today:
        state = dict(state)
        state["day"] = today
        state["dispatches_today"] = 0
    return state


# ── pure decision logic ───────────────────────────────────────────────────────


def should_dispatch(
    state: dict[str, Any],
    cfg: dict[str, Any],
    now: float,
    *,
    killswitch: bool,
) -> Decision:
    """Decide whether a dispatch is allowed right now. Pure."""
    if killswitch:
        return Decision(False, "kill-switch present")
    if not cfg.get("enabled"):
        return Decision(False, "optimizer disabled")

    state = roll_over_day(state, now)
    cap = int(cfg.get("max_dispatches_per_day", 0))
    if state.get("dispatches_today", 0) >= cap:
        return Decision(False, f"daily cap reached ({cap})")

    interval = float(cfg.get("min_interval_seconds", 0))
    elapsed = now - float(state.get("last_run", 0) or 0)
    if elapsed < interval:
        wait = int(interval - elapsed)
        return Decision(False, f"within cooldown ({wait}s left)")

    return Decision(True, "ok")


def select_target(
    opportunities: list[WorkflowOpportunity],
    acted_repos: list[str],
) -> Optional[WorkflowOpportunity]:
    """Highest-scoring opportunity not acted on yet. Pure.

    ``opportunities`` is assumed already score-sorted by ``analyze_sessions``.
    """
    acted = set(acted_repos)
    for opp in opportunities:
        if opp.repo not in acted:
            return opp
    return None


def build_task_spec(opp: WorkflowOpportunity) -> tuple[str, str]:
    """Turn an opportunity into a scoped, defensive task for an agent."""
    kw = ", ".join(k for k, _ in opp.keywords[:5]) or "recurring work"
    repo_name = opp.repo.rstrip("/").rsplit("/", 1)[-1] or opp.repo
    title = f"Automate recurring workflow in {repo_name}: {kw}"[:80]
    samples = "\n".join(f"  - {s}" for s in opp.sample_labels)
    description = (
        f"The session registry shows {opp.session_count} agent sessions in "
        f"`{opp.repo}` clustering around: {kw}.\n\n"
        f"Representative tasks:\n{samples}\n\n"
        "Goal: design ONE reusable automation that would make this recurring "
        "work faster next time. Concretely:\n"
        f"  1. Write a markdown playbook at `docs/workflows/{repo_name}-"
        f"{(opp.keywords[0][0] if opp.keywords else 'workflow')}.md` describing "
        "the workflow step by step.\n"
        "  2. If — and only if — there is an obviously safe, mechanical helper "
        "(a script or a saved slash-command), add it.\n\n"
        "Constraints: do NOT modify unrelated code, do NOT delete data, do NOT "
        "touch credentials or external services. Keep the change small and "
        "self-contained. If nothing safe and useful can be built, write the "
        "playbook only and call done explaining why."
    )
    return title, description


# ── audit ─────────────────────────────────────────────────────────────────────


def _audit(entry: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ── effectful entry point ─────────────────────────────────────────────────────


def maybe_run(now: Optional[float] = None) -> dict[str, Any]:
    """One self-improvement tick. Never raises — safe for the heartbeat."""
    now = time.time() if now is None else now
    try:
        cfg = _config()
        state = load_state()
        decision = should_dispatch(
            state, cfg, now, killswitch=KILLSWITCH_PATH.exists(),
        )
        if not decision.ok:
            return {"dispatched": False, "reason": decision.reason}

        from agent_interface.db import get_connection as base_conn
        opportunities = analyze_sessions(base_conn())
        target = select_target(opportunities, state.get("acted_repos", []))
        if target is None:
            return {"dispatched": False, "reason": "no fresh opportunity"}

        result = _dispatch_for(target, cfg)

        state = roll_over_day(state, now)
        state["last_run"] = now
        state["dispatches_today"] = state.get("dispatches_today", 0) + 1
        state.setdefault("acted_repos", []).append(target.repo)
        save_state(state)

        entry = {
            "ts": now, "repo": target.repo, "score": round(target.score, 2),
            "task_id": result.get("task_id"), "session_id": result.get("session_id"),
        }
        _audit(entry)
        return {"dispatched": True, **entry}
    except Exception as e:  # noqa: BLE001 — heartbeat must never crash
        _audit({"ts": now, "error": f"{type(e).__name__}: {e}"})
        return {"dispatched": False, "reason": f"error: {type(e).__name__}"}


def _ensure_tmux_session(name: str = "agi") -> None:
    """Make sure a detached tmux session exists for dispatched agents.

    The heartbeat runs under systemd (no attached terminal), so dispatch falls
    back to the 'agi' session name. If it doesn't exist, `tmux new-window`
    fails and every tick would error. Create it once, detached.
    """
    import subprocess

    has = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True, timeout=10,
    )
    if has.returncode != 0:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name],
            capture_output=True, timeout=10,
        )


def _dispatch_for(opp: WorkflowOpportunity, cfg: dict[str, Any]) -> dict[str, Any]:
    """Create a ready task for the opportunity and dispatch an agent to it."""
    from agent_interface.orchestrator import core
    from agent_interface.orchestrator.db import get_connection
    from agent_interface.orchestrator.dispatch import dispatch_task

    _ensure_tmux_session("agi")
    conn = get_connection()
    project_name = cfg.get("project_name", "agi-self-improve")
    proj = core.get_project(conn, project_name) or core.create_project(
        conn, project_name,
        description="Autonomous workflow optimizations proposed by agi.",
        autonomy="full",
    )

    title, description = build_task_spec(opp)
    task = core.add_task(
        conn, proj.id, title,
        description=description,
        tags=["self-improve"],
        creator="optimizer",
        status="ready",
    )

    cwd = cfg.get("dispatch_cwd") or _default_repo()
    res = dispatch_task(task.id, cwd=cwd, worktree=True)
    return {"task_id": task.id, "session_id": res.session_id}


def _default_repo() -> str:
    """The agent-interface repo root — where self-improvement work lands."""
    return str(Path(__file__).resolve().parent.parent.parent)
