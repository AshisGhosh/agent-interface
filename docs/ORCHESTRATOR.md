# Orchestrator — design doc

Status: **draft / in progress**. First slice landing now.

The orchestrator is the task-management layer of `agi`. It turns the session registry into a workbench where multiple agents (local or remote, ephemeral or standing) can asynchronously pull tasks off a shared board, report structured progress, and surface blockers back to the user.

This document is the canonical reference for *what* the orchestrator is and *why* it's shaped the way it is. Implementation details live in code; this doc is the contract between design and code.

---

## Why this exists

`agi` today tells you *who* is running and *where*. It does not tell you *what* they should be doing. When you have:

- multiple parallel directions in a model-training project (infra, data, LR sweeps, arch sweeps, eval)
- a game project with a mix of agent-doable work and human-only playtesting
- agents in worktrees discovering follow-up work that needs triage

…you need a shared, durable task graph. The orchestrator provides it.

## Relationship to `agi`

The orchestrator is **built inside `agent-interface`** for now. It:

- lives in `src/agent_interface/orchestrator/`
- shares the same SQLite file as the session registry
- links tasks to sessions (but doesn't require them)
- is exposed through `agi` subcommands, not a separate binary

Separation is a non-goal today but an explicit design constraint. The subpackage must be extractable later with minimal churn:

- orchestrator code does not modify `agent_interface.*` internals
- orchestrator tables are owned entirely by `orchestrator/db.py`
- all cross-boundary reads go through documented public functions in `agent_interface` (not raw SQL against `sessions`)
- orchestrator has its own state enum, its own event log, its own models

If we split it into its own package later, the seam is `agent_interface.registry` + a small hook API. Everything else moves as a unit.

## Architecture

```
 you                    agents
  │                       │
  ├─ agi CLI              ├─ MCP tools (agi mcp)
  ├─ web UI (later)       │  claim_next, progress,
  ├─ Telegram             │  block, done, …
  │                       │
  └──────┬────────────────┘
         ▼
   orchestrator core
   (typed Python ops)
         │
         ▼
   SQLite  (shared with session registry)
   ├─ projects
   ├─ tasks
   ├─ task_events   (append-only)
   ├─ task_notes
   ├─ task_deps
   └─ sessions / events    (existing agi tables)
```

One core library. Multiple surfaces (CLI, MCP, web). The *operation registry* is shared so CLI verbs, MCP tools, and HTTP endpoints can be generated from one source of truth. MCP is the primary agent interface; the CLI is the primary user interface.

## Core concepts

- **Project** — top-level container with a human name, notes, and an autonomy setting. Example: `llm-sft`, `game-dev`.
- **Task** — a unit of work with a status, priority, tags, dependencies, optional description (markdown), optional assigned session, and a timeline of events.
- **TaskEvent** — append-only record of everything that happened to a task. `status` is a materialized view derived from events.
- **TaskNote** — free-form comments from user or agent. Non-structural.
- **Dependency** — a directed edge: task A depends on task B. A becomes `ready` only when all its deps are `done`.

## Status model

```
backlog → ready → in_progress → review → done
             │         │           │
             ↓         ↓           ↓
           blocked ←───┴───────────┘
                    ↑
                  reopened
```

- `backlog` — exists but not yet promoted. Agent-created tasks land here by default.
- `ready` — deps satisfied, eligible for claim.
- `in_progress` — a session has claimed it and is working.
- `review` — agent thinks it's done; user approval required (per project policy).
- `done` — closed.
- `blocked` — parked; needs user / dep / resource. Structured reason.

Status is never written directly. It is derived from the latest terminal event in `task_events`. This makes the timeline the source of truth and lets us replay, audit, and debug agent behavior.

## Session ↔ task binding

A task may have an `assigned_session_id`. Two ways it gets set:

1. **Ephemeral dispatch** (user or orchestrator drives): `agi dispatch <task>` creates a worktree + tmux window + Claude session bound to one task via `AGI_TASK_ID` in the environment. SessionStart hook injects the assignment.
2. **Standing claim** (agent drives): an existing session calls `claim_next` and is bound to whatever task it picked.

**Invariant**: a session holds at most one active task. It can complete task A and then claim task B, but cannot hold both.

**SessionStop hook** auto-blocks any open task whose session ended without calling `done` or `block`. Nothing disappears silently.

## Reliability principles

1. **Typed verbs, not prose status.** Agents call `progress(note)`, `block(reason, needs)`, `done(summary)` — not free-text field edits. The schema is enforced at the MCP/CLI boundary.
2. **Append-only event log.** State is derived. Corruption is recoverable by replay.
3. **Structural participation over remembered instructions.** SessionStart hooks inject task context; MCP advertises tools. Agents don't need to remember to do anything — the environment hands it to them.
4. **Hook-enforced closeout.** SessionStop guarantees no orphaned tasks.
5. **Creation is cheap, execution is gated.** Agents freely add subtasks/peers (they land in `backlog`). Only user promotion or explicit project `autonomy` setting makes them claimable/dispatchable. Breaks the runaway-fanout loop.

## Agent-created work

Two MCP tools, different trust levels:

- `add_subtask(parent_id, title, …)` — child of current task. Cheap.
- `add_task(project_id, title, …)` — peer task. Enters `backlog`, requires user triage (`agi triage`).

Every task has a `creator` field (`user` / `session:<id>` / `hook`) and a `spawned_from` link so you can trace *why* a task exists.

A session's subtask creation is soft-quota-limited per project; excess produces a warning event, not a hard block.

## Agent dispatch (future)

Projects carry an `autonomy` setting: `none | subtasks_only | full`. Only with `full` can an agent call `dispatch(task_id)` to spawn another Claude session for a task. Even then:

- only `ready` tasks can be dispatched (not backlog the agent just created)
- a project-level `dispatch_budget` caps total fan-out before you approve more
- `max_dispatch_depth` caps recursion

This is **not in the first slice**. It's documented here so the surrounding design (creator tracking, backlog gating) makes sense.

## CLI shape

Hot-path verbs are flat; admin is namespaced.

**Hot-path (what you and agents type constantly):**
- `agi board [project]` — rich kanban view
- `agi next [--project X]` — claim / recommend next task
- `agi done <task> --summary …`
- `agi block <task> --reason … --needs user|dep|resource`
- `agi progress <task> --note …`

**Admin:**
- `agi projects new <name>`
- `agi projects list`
- `agi tasks add <title> --project X [--priority N] [--tags a,b] [--depends-on T1,T2]`
- `agi tasks list [--project X] [--status s]`
- `agi tasks show <id>`
- `agi tasks edit <id>` (opens `$EDITOR` on description)
- `agi tasks set <id> --priority …`
- `agi tasks link <id> --depends-on <other>`
- `agi triage` — agent-created tasks awaiting review
- `agi review` — tasks in `review` status awaiting approval

**Agent-only (via MCP):** same verbs, typed arguments, discovered at session start.

## Data model (first slice)

```
projects
  id TEXT PK, name TEXT UNIQUE, description TEXT,
  autonomy TEXT NOT NULL DEFAULT 'none',
  created_at TEXT, updated_at TEXT, archived_at TEXT

tasks
  id TEXT PK, project_id TEXT FK, parent_id TEXT FK NULL,
  title TEXT, description TEXT,
  status TEXT NOT NULL,  -- derived cache; truth is in task_events
  priority INTEGER NOT NULL DEFAULT 2,
  tags TEXT,             -- comma-separated for simplicity
  creator TEXT NOT NULL, -- 'user' | 'session:<id>' | 'hook'
  spawned_from_task TEXT NULL,
  spawned_from_session TEXT NULL,
  assigned_session_id TEXT NULL,
  worktree_path TEXT NULL,
  created_at TEXT, updated_at TEXT, closed_at TEXT

task_events
  id INTEGER PK AUTOINCREMENT,
  task_id TEXT FK, event_type TEXT,
  payload_json TEXT, created_at TEXT,
  actor TEXT  -- 'user' | 'session:<id>' | 'system'

task_deps
  task_id TEXT FK, depends_on_task_id TEXT FK,
  PRIMARY KEY (task_id, depends_on_task_id)

task_notes
  id INTEGER PK, task_id TEXT FK, author TEXT,
  body TEXT, created_at TEXT
```

All tables live in the same SQLite file as sessions. Foreign keys to `sessions(id)` are declared as soft references (no ON DELETE) to keep the modules decoupled.

## Event vocabulary (tasks)

- `created` — task exists
- `ready` — became claimable (deps satisfied or manual promote)
- `claimed` — session took it
- `progress` — note or pct update
- `artifact` — typed output registered
- `blocked` / `unblocked` — with reason/needs
- `review_requested` — agent flags for human approval
- `approved` / `rejected`
- `done` — closed
- `reopened` — moved from done back to in_progress
- `subtask_added` / `task_spawned` — fan-out tracking
- `assigned` / `unassigned` — session binding changes

## Slices (build order)

**Slice 1 (this PR):**
- schema + migrations
- core ops: `create_project`, `add_task`, `claim_next`, `progress`, `block`, `unblock`, `done`, `reopen`, `list_tasks`
- CLI: `projects new/list`, `tasks add/list/show`, `next`, `done`, `block`, `progress`, `board` (basic)
- tests covering the ops
- no MCP yet, no hooks yet, no dispatch, no web UI, no markdown plan

**Slice 2:** MCP server (`agi mcp`) exposing the ops as typed tools. SessionStart/Stop hook integration to inject assignments and auto-block orphans.

**Slice 3:** `dispatch` (ephemeral worktree+tmux+Claude spawn bound to a task). `agi triage` / `agi review` UX. Markdown plan round-trip.

**Slice 4:** Agent-dispatch (`dispatch` MCP tool) with autonomy/budget/depth guardrails. `plan_and_dispatch` for bounded fan-out.

**Slice 5:** Local web UI (FastAPI) for board + task detail + drag-to-reorder. Telegram `/board`, `/blocked`, `/review`.

## Non-goals

- Multi-user / auth
- Distributed scheduler behavior across machines in one project
- Replacing git, tmux, or Claude Code
- Automatic merging or code review
- A workflow engine with branches, retries, timeouts (tasks aren't workflow steps)

## Open questions

- Exact policy for auto-promoting `backlog → ready` when dependencies close. First slice: manual only, to keep the agent-creation firewall tight. Slice 2 may add an opt-in auto-promote for user-created tasks.
- Whether `review` should be a distinct status or a tag on `done` pending approval. Current call: distinct status.
- Per-project dispatch budgets: where to store and how to refill. Deferred to slice 4.
