# Agent Conductor

A remote-first control plane for Claude Code sessions.

This project exists to make parallel coding agents easier to manage when they are running over SSH on desktops, servers, or clusters. The goal is not to replace Claude Code, tmux, git, or worktrees. The goal is to add a thin layer of visibility, triage, and navigation on top of them.

## Project identity

- Project name: `agent-interface`
- CLI name: `agi`
- Expansion: `Agent Interface`

## Why this exists

My current workflow is strong on execution but weak on supervision.

Typical setup:
- SSH into one or more remote machines
- Run Claude Code in tmux sessions
- Sometimes use git worktrees for isolation
- Follow up later from another terminal or phone

This works, but becomes cumbersome when multiple agents are active at once.

The core pain points are:
- It is hard to see what all active agents are doing
- It is hard to tell which agent is waiting for a response
- It is annoying to jump back into the correct session quickly
- Worktrees become messy over time
- Ad hoc sessions are easy to lose track of

This project solves those problems.

## Product vision

Agent Conductor is a lightweight remote-first session registry and control surface for agentic coding workflows.

It should:
- Automatically register Claude sessions, including ad hoc ones
- Track status for each session
- Highlight sessions that need user input
- Make it easy to jump back into the right host, tmux session, and directory
- Support managed worktree-based tasks, but not require them
- Keep a clean lifecycle for sessions and tasks
- Stay simple enough to run entirely from the terminal at first

## Design principles

### Remote-first
The source of truth should live on the machine where the agent is running, not on the local laptop.

### Session-first
Any Claude session can be tracked, even if it was started manually and does not use a worktree.

### Worktree-friendly
Managed tasks should use worktrees cleanly, but worktrees are optional.

### Thin control plane
This project is not an IDE, not a scheduler, and not an agent framework. It is a visibility and orchestration layer.

### Graceful escalation
Start as a CLI with SQLite and optional JSON logs. Add web or mobile views later only if useful.

### Strong observability
A session should not be invisible just because it lives in a tmux pane on a remote host.

## Scope

### In scope
- Session registration
- Session updates
- State tracking
- Waiting-for-user detection
- Listing and filtering sessions
- Renaming and archiving sessions
- Basic jump or re-entry commands
- Optional worktree helpers
- Optional tmux helpers
- SQLite-backed registry
- Event ingestion from Claude session lifecycle hooks or wrappers

### Out of scope for MVP
- Full web UI
- Multi-user support
- Authentication
- Distributed scheduler behavior
- Automatic merging or code review
- Deep editor integrations
- Complex daemon architecture
- Full cluster orchestration
- Replacing tmux
- Replacing git worktrees
- Running the agents itself

## Installation model

This tool should be usable across many existing repositories.

That means the primary usage model is not:
- creating a fresh venv inside every target repo
- installing `agent-interface` separately into each project environment

Instead, the tool should be:

- developed as a normal Python project
- installed once as a CLI tool
- usable from any repo or working directory

## Recommended environment model

### For normal usage
Install `agi` once as a tool so it is available from any shell.

Example shape:
- install once
- run `agi` from any project
- keep project-specific environments separate from the tool itself

### For development of `agent-interface`
Use a normal project-local uv workflow.

Typical development flow:
- `uv sync`
- `uv run agi --help`
- `uv run pytest`

## Requirements

- Python 3.11+
- `uv`
- optional: `git`
- optional: `tmux`
- optional: Claude Code installed separately

## Expected install and run flow

### Normal usage
Install the tool once from the `agent-interface` source directory:

    uv tool install .

After that, the CLI should be available as:

    agi --help

Later, when the project is hosted remotely, installation should also support a git-based tool install.

### Development
From the `agent-interface` repo:

    uv sync
    uv run agi --help
    uv run pytest

## User stories

### Ad hoc session
As a user, I can SSH into any machine, launch Claude Code in a random directory, and have that session show up in the registry with a basic identity and state.

### Managed task
As a user, I can start a named task in a worktree and have it appear with cleaner metadata and lifecycle management.

### Needs attention
As a user, I can quickly see which sessions are waiting for my response.

### Fast re-entry
As a user, I can jump into the correct host and tmux session without manually remembering where that task is running.

### Cleanup
As a user, I can archive or prune stale sessions so the system stays readable.

## Core concepts

### Session
A live or recently live Claude coding context.

A session may be:
- ad hoc
- managed
- in a repo
- outside a repo
- in tmux
- outside tmux

A session is the primary tracked object.

### Task
A human-friendly label associated with a session or worktree. A task is optional at registration time, but can be added later.

### Managed session
A session started through this tool's wrapper commands, typically with a worktree and better metadata.

### Ad hoc session
A session started manually that is auto-discovered and auto-registered.

### Registry
The persistent store of sessions, state, timestamps, metadata, and events.

### Event
A state update or lifecycle signal for a session.

Examples:
- `session_started`
- `heartbeat`
- `state_changed`
- `waiting_for_user`
- `blocked`
- `done`
- `archived`

## Supported states

The system should support a small explicit state machine.

### Canonical session states
- `running`
- `waiting_for_user`
- `blocked`
- `tests_failed`
- `done`
- `idle`
- `archived`
- `stale`
- `unknown`

### Notes
- `waiting_for_user` is the highest-value state
- `stale` is a derived state based on inactivity
- `archived` means hidden from normal active views
- `unknown` is acceptable for partial or malformed inputs during early development

## Session identity

The registry must be able to identify a session without requiring a worktree.

A session identity should be derived from the strongest available signals:
- host
- current working directory
- repo root if any
- git branch if any
- tmux session, window, and pane if any
- process id if useful
- session id from wrapper or hook if available

The system should tolerate partial identity and still register the session.

The system should prefer stable identity over perfect identity.

## Registration model

Sessions may enter the system in two ways.

### 1. Wrapper-based registration
The user launches a session through a project command such as:
- `agi task start sim-eval`
- `agi session start`

This creates the initial registry entry directly.

### 2. Hook-based auto-registration
A Claude session started normally triggers a hook or ingest script. If the session is not already known, the system creates a registry entry automatically.

Wrapper-based registration is cleaner.
Hook-based registration is essential for ad hoc sessions.

Both must be supported.

## Storage

### Primary storage
SQLite

Why:
- simple
- local
- queryable
- transactional enough for this use case
- easy to inspect manually
- easy to evolve

### Optional side storage
JSON event logs for debugging and replay.

## Proposed CLI

The CLI name is `agi`.

### Session commands
- `agi sessions list`
- `agi sessions waiting`
- `agi sessions show <session_id>`
- `agi sessions rename <session_id> <label>`
- `agi sessions archive <session_id>`
- `agi sessions restore <session_id>`
- `agi sessions prune`
- `agi sessions heartbeat <session_id>`
- `agi sessions update-state <session_id> <state>`
- `agi sessions register --cwd ... --host ...`

### Task commands
- `agi task start <name>`
- `agi task open <name-or-id>`
- `agi task close <name-or-id>`
- `agi task list`

### Worktree commands
- `agi wt create <name>`
- `agi wt list`
- `agi wt remove <name>`

### Debug commands
- `agi ingest event <path>`
- `agi doctor`
- `agi dump`
- `agi schema`

## Example workflows

### Workflow 1: random ad hoc Claude session
1. SSH into remote machine
2. Change into some folder
3. Launch Claude Code normally
4. Hook or wrapper emits registration event
5. Session appears in `agi sessions list`
6. If Claude later needs user input, state becomes `waiting_for_user`
7. User can inspect and jump back in

### Workflow 2: managed task with worktree
1. Run `agi task start planner-refactor`
2. Tool creates worktree and branch if appropriate
3. Tool opens tmux window and launches Claude
4. Session is registered immediately
5. State updates over time
6. User later runs `agi sessions waiting`
7. User runs `agi task open planner-refactor`
8. User responds and continues
9. Task is closed and optionally archived

### Workflow 3: stale session cleanup
1. Session stops updating
2. Derived stale logic marks it `stale`
3. User reviews it
4. User archives or prunes it

## Data model

The exact schema may evolve, but the MVP should roughly capture:

### Sessions table
- `id`
- `label`
- `host`
- `cwd`
- `repo_root`
- `branch`
- `tmux_session`
- `tmux_window`
- `tmux_pane`
- `worktree_path`
- `is_managed`
- `state`
- `summary`
- `created_at`
- `updated_at`
- `last_seen_at`
- `archived_at`

### Events table
- `id`
- `session_id`
- `event_type`
- `payload_json`
- `created_at`

### Optional tasks table
- `id`
- `name`
- `session_id`
- `status`
- `created_at`
- `updated_at`

## Example session object

    {
      "id": "gpu-box-1:/home/ashis/code/foo:dev:2.1",
      "label": "planner-refactor",
      "host": "gpu-box-1",
      "cwd": "/home/ashis/code/foo",
      "repo_root": "/home/ashis/code/foo",
      "branch": "main",
      "tmux_session": "dev",
      "tmux_window": "planner",
      "tmux_pane": "2.1",
      "worktree_path": null,
      "is_managed": false,
      "state": "waiting_for_user",
      "summary": "Need decision on API boundary between planner and executor",
      "created_at": "2026-04-04T18:00:00Z",
      "updated_at": "2026-04-04T18:10:00Z",
      "last_seen_at": "2026-04-04T18:10:00Z",
      "archived_at": null
    }

## State transitions

Allowed transitions should be simple and forgiving.

Typical examples:
- `unknown -> running`
- `running -> waiting_for_user`
- `running -> blocked`
- `running -> tests_failed`
- `running -> done`
- `waiting_for_user -> running`
- `blocked -> running`
- `tests_failed -> running`
- `done -> archived`
- any active state -> `stale` if timed out
- `stale -> archived`
- `archived -> idle` or previous visible state on restore

The system should avoid complex enforcement in early versions. It is acceptable to allow manual overrides.

## MVP requirements

The MVP is successful if it can do the following reliably:

1. Register a session manually
2. Register an ad hoc session automatically through an ingest path
3. Persist sessions in SQLite
4. Show all active sessions
5. Show sessions in `waiting_for_user`
6. Rename a session for clarity
7. Archive a session
8. Mark stale sessions based on inactivity
9. Preserve an event log for debugging
10. Support enough metadata to identify where the session lives

## Non-functional requirements

- Must work from terminal only
- Must be easy to inspect and debug
- Must not require a daemon for MVP
- Must tolerate partial metadata
- Must not break if tmux is absent
- Must not require git or worktrees
- Must remain useful even with only ad hoc sessions
- Must be easy to extend later into a TUI or web UI

## Suggested implementation stack

- Python 3.11+
- `sqlite3` standard library or a light wrapper
- `typer` or `argparse` for CLI
- `pydantic` optional for schema validation
- `pytest` for tests
- no heavy framework for MVP

## Packaging and naming

Recommended naming split:
- repo: `agent-interface`
- Python package: `agent_interface`
- CLI command: `agi`

This keeps the package name Python-friendly while keeping the terminal command short.

## Repository layout

    .
    ├── README.md
    ├── IMPLEMENTATION_PLAN.md
    ├── STARTER_PROMPT.md
    ├── pyproject.toml
    ├── src/
    │   └── agent_interface/
    │       ├── __init__.py
    │       ├── cli.py
    │       ├── db.py
    │       ├── models.py
    │       ├── registry.py
    │       ├── states.py
    │       ├── ingest.py
    │       ├── detect.py
    │       ├── tmux.py
    │       ├── worktree.py
    │       ├── jump.py
    │       └── utils.py
    ├── tests/
    │   ├── test_registry.py
    │   ├── test_states.py
    │   ├── test_cli.py
    │   ├── test_ingest.py
    │   └── fixtures/
    └── scripts/
        ├── example_event.json
        └── dev_seed.py

## Future milestones after MVP

### Phase 2
- automatic hook ingestion
- tmux-aware jump command
- better repo and git detection
- managed task wrapper

### Phase 3
- worktree lifecycle helpers
- stale pruning
- notes and tags
- better event summaries

### Phase 4
- TUI
- tiny phone-friendly read-only web view
- optional multi-host aggregation

## Success criteria

This project is successful when:
- I can tell what my active agents are doing with one command
- I can tell which agents need my response with one command
- I can jump back into the correct session quickly
- I stop losing track of ad hoc sessions
- worktree-backed tasks become cleaner instead of more annoying

## Development guidance for the coding agent

When implementing this project:
- prefer simple, inspectable code over clever abstractions
- keep MVP terminal-only
- do not introduce unnecessary async complexity
- do not build a web app yet
- do not over-engineer distributed coordination
- keep session registration robust even with partial data
- make the SQLite schema clear and easy to migrate
- add tests alongside each feature
- ship a working narrow slice first, then expand

## Explicit anti-goals for the coding agent

Do not:
- build a full dashboard first
- build auth
- build a daemon unless truly needed
- build multi-host coordination before single-host works
- require Claude-specific internals for every feature
- assume worktrees are always present
- overcomplicate state transitions
- create large abstractions before the CLI loop works

## First milestone

Build the smallest useful system:
- SQLite-backed session registry
- manual session registration
- manual state updates
- `sessions list`
- `sessions waiting`
- `sessions rename`
- `sessions archive`
- tests for all of the above

Once that works, add event ingestion and auto-registration.