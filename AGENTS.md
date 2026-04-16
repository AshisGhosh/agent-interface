# AGENTS.md

Orientation for agents (and humans) working in this repo.

## What this project is

`agent-interface` is the home of two cooperating pieces:

1. **`agi` — session registry.** Tracks running coding-agent sessions across hosts, tmux panes, and worktrees. Highlights sessions that are waiting for the user. Jumps you back into the right pane.
2. **Orchestrator (folded into `agi`).** A task-management layer on top of the registry. Projects, tasks with a status/deps/priority graph, typed event log, and a small MCP surface so agents can claim work, report progress, and surface blockers asynchronously.

The CLI is `agi`. There is no second binary.

## High-level architecture

- Single SQLite file (`~/.local/share/agent-interface/registry.db`) holds both session and orchestrator data.
- Session code lives in `src/agent_interface/`.
- Orchestrator code lives in `src/agent_interface/orchestrator/` as a self-contained subpackage. It is designed so it can be extracted into its own repo later with minimal churn — treat the subpackage boundary as a hard seam.
- CLI is Typer (`src/agent_interface/cli.py`).
- MCP server (slice 2+) will live at `src/agent_interface/mcp_server.py` and reuse the orchestrator core directly.

## Core principles

- **Append-only event logs are the source of truth.** Status columns on `tasks` and `sessions` are caches. Never trust them over the event log when debugging.
- **Typed verbs, no prose status updates.** Agents mutate state via `progress`, `block`, `done` — not by editing text fields.
- **Creation is cheap, execution is gated.** Agents can freely create subtasks and peer tasks; they land in `backlog` and require user promotion (or explicit project autonomy) before they're claimable.
- **Structural participation beats remembered instructions.** Use hooks and MCP tool discovery to hand context to agents, rather than relying on CLAUDE.md-style standing instructions.
- **Keep the subpackage boundary clean.** Orchestrator code does not modify session internals directly; session code does not import from `orchestrator/`. Cross-boundary reads go through the public `agent_interface.registry` API.

## Where to read next

- [README.md](README.md) — session-registry product vision and CLI surface.
- [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md) — orchestrator design doc, including data model, status machine, slice plan, and open questions.
- [src/agent_interface/orchestrator/](src/agent_interface/orchestrator/) — orchestrator implementation.

## For agents working in this repo

- Before writing code, read `docs/ORCHESTRATOR.md` if the task touches tasks, projects, events, or MCP.
- Prefer small, reviewable slices. The slice plan in the design doc is intentional — don't leapfrog it.
- Tests go next to the module (see `tests/`). Use the `conn` fixture in `tests/conftest.py` for in-memory SQLite.
- Don't introduce async unless an operation genuinely needs it. The codebase is synchronous by design.
- Keep CLI hot-path verbs flat (`agi next`, `agi done`). Namespace only admin (`agi tasks add`).
