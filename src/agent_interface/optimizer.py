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


def build_task_spec(opp: WorkflowOpportunity, *, feature_id: str) -> tuple[str, str]:
    """Turn a cross-project use-case into a scoped agi-feature task.

    The feature is built IN agent-interface (the cross-project agi tooling) but
    must help agents working in OTHER projects — inspired by the real activity
    `opp` represents. It must be genuinely usable, and instrument its own usage
    so the loop can later tell whether it actually got used.
    """
    kw = ", ".join(k for k, _ in opp.keywords[:5]) or "recurring work"
    title = f"Ship agi feature for cross-project use: {kw}"[:80]
    samples = "\n".join(f"  - {s}" for s in opp.sample_labels)
    description = (
        "Ship a small, genuinely useful **agi feature** (a new `agi` subcommand "
        "or capability in THIS repo, agent-interface) that helps coding agents "
        f"working in OTHER projects — inspired by real activity in `{opp.repo}` "
        f"({opp.session_count} sessions) clustering around: {kw}.\n\n"
        f"Representative tasks observed there:\n{samples}\n\n"
        "Requirements:\n"
        "  1. Implement the feature in `src/agent_interface/` and wire it into "
        "the CLI (`cli.py`) as a new `agi` command/flag. It must work from ANY "
        "project directory, not just agent-interface.\n"
        "  2. Instrument usage: at the feature's entry point call "
        f"`from agent_interface.usage import record_usage; record_usage('{feature_id}')` "
        "so the loop can verify it actually gets used.\n"
        "  3. Add tests under `tests/` and keep `scripts/preflight.sh` green.\n"
        "  4. Document the command in README.md.\n\n"
        "Constraints: ship ONE focused, working feature (not a doc). Do NOT "
        "modify unrelated code, delete data, or touch credentials/external "
        "services. Make sure preflight passes before calling done."
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
        # Use cases come from OTHER projects' agent activity — exclude
        # agent-interface itself, since features must help external work.
        repo = cfg.get("dispatch_cwd") or _default_repo()
        opportunities = [
            o for o in analyze_sessions(base_conn()) if o.repo.rstrip("/") != repo.rstrip("/")
        ]
        target = select_target(opportunities, state.get("acted_repos", []))
        if target is None:
            return {"dispatched": False, "reason": "no fresh cross-project opportunity"}

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

    import uuid
    feature_id = f"feat-{uuid.uuid4().hex[:8]}"
    title, description = build_task_spec(opp, feature_id=feature_id)
    task = core.add_task(
        conn, proj.id, title,
        description=description,
        tags=["self-improve", "feature"],
        creator="optimizer",
        status="ready",
    )

    cwd = cfg.get("dispatch_cwd") or _default_repo()
    res = dispatch_task(task.id, cwd=cwd, worktree=True)

    # Register the feature so the loop can later judge whether it got used.
    from agent_interface import features
    features.register(feature_id, title, task_id=task.id, helps=opp.repo)

    try:
        from agent_interface.telegram import send_message
        send_message(f"🚀 <b>self-improve</b> dispatched feature: {title[:65]}")
    except Exception:  # noqa: BLE001
        pass
    return {"task_id": task.id, "session_id": res.session_id, "feature_id": feature_id}


def _default_repo() -> str:
    """The agent-interface repo root — where self-improvement work lands."""
    return str(Path(__file__).resolve().parent.parent.parent)


# ── delivery safety-net: ensure completed work actually lands on main ──────────
#
# The orchestrator squash-merges a finished task to main only when a worktree is
# checked out on main. If it isn't (e.g. mid-feature-branch), the merge is
# silently *skipped* and the improvement strands on its task/<id> branch —
# delivered but never used. This net detects those stragglers and lands the
# fast-forwardable, test-passing ones, notifying either way.


def _git(args: list[str], repo: str, timeout: int = 120) -> Any:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
    )


def pending_deliveries(conn, repo: str) -> list[dict[str, str]]:
    """Done self-improve tasks whose task/<id> branch exists but isn't on main."""
    project = DEFAULTS["project_name"]
    rows = conn.execute(
        """SELECT t.id AS id, t.title AS title
           FROM tasks t JOIN projects p ON t.project_id = p.id
           WHERE p.name = ? AND t.status = 'done'""",
        (project,),
    ).fetchall()

    out: list[dict[str, str]] = []
    for r in rows:
        branch = f"task/{r['id']}"
        if _git(["rev-parse", "--verify", "--quiet", branch], repo).returncode != 0:
            continue  # branch gone — already merged and cleaned up
        if _git(["merge-base", "--is-ancestor", branch, "main"], repo).returncode == 0:
            continue  # already contained in main
        out.append({"id": r["id"], "title": r["title"], "branch": branch})
    return out


def _repo_idle_on_main(repo: str) -> bool:
    """Only touch the repo when it's safe: clean tree, currently on main."""
    head = _git(["symbolic-ref", "--short", "HEAD"], repo)
    clean = not _git(["status", "--porcelain"], repo).stdout.strip()
    return head.stdout.strip() == "main" and clean


def _preflight_ok(repo: str) -> bool:
    """Run the repo's preflight gate (ruff + tests). Missing script → skip gate."""
    script = Path(repo) / "scripts" / "preflight.sh"
    if not script.exists():
        return True
    import subprocess

    return subprocess.run(
        ["bash", str(script)], cwd=repo, capture_output=True, text=True, timeout=900,
    ).returncode == 0


def deliver_pending(repo: Optional[str] = None, *, notify: bool = True) -> dict[str, list]:
    """Land stranded self-improve branches on main when fast-forward + green.

    Conservative on purpose: fast-forward only (never auto-resolve conflicts),
    and roll the merge back if the test gate fails. Anything not landed is
    flagged for the user.
    """
    repo = repo or _default_repo()
    landed: list[dict] = []
    flagged: list[dict] = []
    try:
        from agent_interface.orchestrator.db import get_connection
        conn = get_connection()
        items = pending_deliveries(conn, repo)
    except Exception:  # noqa: BLE001
        return {"landed": landed, "flagged": flagged}

    for item in items:
        branch = item["branch"]
        ff_ok = _git(["merge-base", "--is-ancestor", "main", branch], repo).returncode == 0
        if not (ff_ok and _repo_idle_on_main(repo)):
            flagged.append({**item, "reason": "not fast-forwardable or repo busy"})
            continue

        prev = _git(["rev-parse", "main"], repo).stdout.strip()
        if _git(["merge", "--ff-only", branch], repo).returncode != 0:
            flagged.append({**item, "reason": "fast-forward failed"})
            continue
        if _preflight_ok(repo):
            _git(["branch", "-d", branch], repo)
            landed.append(item)
            _audit({"deliver": "landed", "task": item["id"]})
        else:
            _git(["reset", "--hard", prev], repo)  # roll back the bad merge
            flagged.append({**item, "reason": "test gate failed (rolled back)"})
            _audit({"deliver": "rolled_back", "task": item["id"]})

    if notify and (landed or flagged):
        _notify_deliveries(landed, flagged)
    return {"landed": landed, "flagged": flagged}


def _notify_deliveries(landed: list[dict], flagged: list[dict]) -> None:
    try:
        from agent_interface.telegram import send_message

        lines = ["🔧 <b>self-improve delivery</b>"]
        for i in landed:
            lines.append(f"✅ landed: {i['title'][:60]}")
        for i in flagged:
            lines.append(
                f"⚠️ needs merge ({i['reason']}): {i['title'][:45]} "
                f"[<code>{i['branch']}</code>]"
            )
        send_message("\n".join(lines))
    except Exception:  # noqa: BLE001
        pass
