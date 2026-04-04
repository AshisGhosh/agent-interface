# Implementation Plan

This file describes how to build Agent Conductor in stages, with explicit testing and verification at each milestone.

The priority is to prove the control loop early:
1. sessions can be registered
2. sessions can update state
3. waiting sessions are easy to find
4. sessions can be renamed, archived, and pruned
5. the system remains simple and inspectable

## Implementation strategy

Build this in narrow, verifiable slices.

Avoid building:
- web UI
- daemon infrastructure
- multi-host sync
- complex background services

The first versions should work entirely through a local SQLite database and a Python CLI.

## Phase 0: project bootstrap

### Goals
- initialize repository
- set up Python project structure
- set up CLI entrypoint
- set up test framework
- establish formatting and linting
- ensure uv-compatible packaging from the start

### Deliverables
- `pyproject.toml`
- package skeleton under `src/agent_interface`
- `pytest` configured
- basic CLI command that prints help
- developer instructions in README
- CLI script entry point named `agi`

### Suggested tasks
1. Initialize package layout
2. Add CLI framework
3. Add test runner
4. Add minimal dev commands
5. Add package metadata and script entry point
6. Make sure `uv sync` and `uv run agi --help` work

### Verification
- `uv sync` completes successfully
- `uv run agi --help` works
- `uv run pytest` runs successfully with at least one smoke test
- `uv tool install .` installs a runnable `agi` command

### Exit criteria
- project can be run and tested locally with no feature work yet
- packaging and install story are coherent

---

## Phase 1: core data model and registry

### Goals
- define session schema
- create SQLite database
- implement basic CRUD for sessions
- implement event logging

### Deliverables
- `db.py`
- `models.py`
- `registry.py`
- schema initialization or migrations
- manual register path

### Required behavior
- create session record
- fetch session by id
- list active sessions
- update session metadata
- archive session
- append event record

### Suggested schema

#### sessions
- id
- label
- host
- cwd
- repo_root
- branch
- tmux_session
- tmux_window
- tmux_pane
- worktree_path
- is_managed
- state
- summary
- created_at
- updated_at
- last_seen_at
- archived_at

#### events
- id
- session_id
- event_type
- payload_json
- created_at

### Verification

#### Unit tests
- create session
- update session
- archive session
- append event
- list active sessions excludes archived sessions by default

#### Manual verification
- register a fake session from CLI
- inspect SQLite contents directly
- confirm timestamps update correctly

### Exit criteria
- registry works without any tmux, git, or Claude-specific integration

---

## Phase 2: state machine and filtering

### Goals
- define canonical states
- implement state update rules
- add filtered views for waiting and stale sessions

### Deliverables
- `states.py`
- registry filtering helpers
- stale derivation logic

### Required behavior
- support `running`, `waiting_for_user`, `blocked`, `tests_failed`, `done`, `idle`, `archived`, `stale`, `unknown`
- support manual override for state
- derive stale based on `last_seen_at`
- return waiting sessions quickly

### Verification

#### Unit tests
- valid transitions
- invalid or unsupported transition handling
- stale derivation
- waiting filter returns only correct sessions

#### Manual verification
- register three sessions
- mark one waiting
- mark one blocked
- age one into stale
- confirm filtered commands behave as expected

### Exit criteria
- state model is clear and queryable

---

## Phase 3: CLI for useful daily operations

### Goals
- make the tool practically usable from terminal
- expose core workflows through clean commands

### Deliverables
- `sessions list`
- `sessions waiting`
- `sessions show`
- `sessions rename`
- `sessions archive`
- `sessions restore`
- `sessions update-state`
- `sessions register`

### UX requirements
- output should be readable in a narrow terminal
- list views should be concise
- show command should include full metadata
- failure messages should be direct and useful

### Verification

#### CLI tests
- each command runs successfully
- error handling for unknown session ids
- waiting view filters correctly
- archive hides session from active list

#### Manual verification
- register sessions from CLI
- rename one
- archive one
- restore one
- inspect list output after each action

### Exit criteria
- first actually useful version exists without any automation

---

## Phase 4: environment detection

### Goals
- detect metadata from the current shell environment
- support ad hoc sessions with partial metadata

### Deliverables
- `detect.py`
- functions for:
  - hostname detection
  - cwd detection
  - git repo detection
  - branch detection
  - tmux environment detection

### Required behavior
- work even if not in git
- work even if not in tmux
- degrade gracefully when metadata is missing

### Verification

#### Unit tests
- detection behavior in fake environments
- git repo parsing
- no-git fallback
- no-tmux fallback

#### Manual verification
Test from:
1. repo in tmux
2. repo outside tmux
3. non-repo in tmux
4. random directory outside tmux

Confirm the tool still creates useful metadata in all four cases.

### Exit criteria
- session identity can be constructed robustly from real shell context

---

## Phase 5: ingest path for auto-registration

### Goals
- create a generic ingest layer for event-driven registration and updates
- support JSON input from hooks or wrapper scripts

### Deliverables
- `ingest.py`
- command like `agi ingest event <path-or-stdin>`
- event-to-registry mapping logic

### Required behavior
- if event references unknown session, create it
- if event references known session, update it
- append event record
- update timestamps
- update state if appropriate

### Suggested event types
- `session_started`
- `heartbeat`
- `state_changed`
- `waiting_for_user`
- `blocked`
- `done`
- `summary_updated`
- `session_archived`

### Example event payload shape

    {
      "event_type": "waiting_for_user",
      "session": {
        "id": "gpu-box-1:/home/ashis/code/foo:dev:2.1",
        "host": "gpu-box-1",
        "cwd": "/home/ashis/code/foo",
        "repo_root": "/home/ashis/code/foo",
        "branch": "main",
        "tmux_session": "dev",
        "tmux_window": "planner",
        "tmux_pane": "2.1",
        "is_managed": false
      },
      "summary": "Need decision on planner executor boundary",
      "timestamp": "2026-04-04T18:10:00Z"
    }

### Verification

#### Unit tests
- ingest new session event creates record
- ingest update event modifies record
- ingest waiting event updates state
- malformed event is rejected cleanly
- duplicate event handling is acceptable and predictable

#### Manual verification
- replay fixture event files into ingest command
- confirm database state after each event
- confirm waiting view updates correctly

### Exit criteria
- system supports auto-registration through a generic event path

---

## Phase 6: wrapper commands for managed sessions

### Goals
- support a clean launch path for named tasks
- create a nicer managed mode without making it mandatory

### Deliverables
- `task start <name>`
- optional `task open`
- optional `task close`

### Required behavior
- task start creates managed session entry
- task may optionally prepare worktree metadata
- task open locates session metadata and prints or executes re-entry command
- task close archives or cleans up session metadata

### Verification

#### Unit tests
- task start creates managed session
- task open resolves correct session
- task close archives session

#### Manual verification
- launch a managed task
- confirm it appears distinctly from ad hoc sessions
- rename and close it cleanly

### Exit criteria
- managed sessions feel cleaner, but ad hoc sessions remain first-class

---

## Phase 7: tmux integration and jump path

### Goals
- make re-entry easy
- integrate with tmux when available

### Deliverables
- `tmux.py`
- `jump.py`
- `task open` or `sessions open` command

### Required behavior
- detect tmux metadata when present
- generate an attach or switch command
- optionally execute it
- fall back gracefully if tmux metadata is missing

### Verification

#### Unit tests
- tmux command generation
- missing tmux metadata fallback
- parsing tmux identifiers

#### Manual verification
From a machine with tmux:
- register a session in tmux
- run open command
- confirm you land in the expected session, window, or pane, or get a correct shell command

### Exit criteria
- jumping back into live sessions is materially easier

---

## Phase 8: stale detection and pruning

### Goals
- prevent registry clutter
- handle dead or abandoned sessions

### Deliverables
- `sessions prune`
- stale marking logic
- archive helpers

### Required behavior
- identify stale sessions based on inactivity threshold
- support archive by policy or manually
- keep event history for archived sessions
- never destroy data silently in MVP

### Verification

#### Unit tests
- stale detection thresholds
- prune selection behavior
- archive path preserves data

#### Manual verification
- seed old sessions
- run prune
- confirm stale or archive behavior works as intended

### Exit criteria
- dashboard remains readable over time

---

## Phase 9: optional worktree helpers

### Goals
- make worktree-backed tasks cleaner
- keep worktrees optional

### Deliverables
- `wt create`
- `wt list`
- `wt remove`

### Required behavior
- detect repo root
- create predictable worktree paths
- associate worktree metadata with session
- degrade gracefully when git is absent

### Verification

#### Unit tests
- worktree metadata modeling
- command generation logic

#### Manual verification
Inside a git repo:
- create worktree
- confirm metadata recorded
- remove worktree cleanly

### Exit criteria
- worktrees stop feeling unmanaged and disconnected

---

## Phase 10: quality pass and polish

### Goals
- make the tool dependable
- simplify output and docs
- improve developer ergonomics

### Deliverables
- improved help text
- robust error messages
- fixture coverage
- README refresh based on real implementation
- dev seed script for fake sessions
- final install instructions for both development and tool use

### Verification

#### Regression tests
- no command regressions
- schema init still works on clean DB
- ingest and list flows still work together
- archive and restore remain consistent
- `uv run agi --help` still works
- `uv tool install .` still yields a working `agi`

#### Manual verification
- start from empty directory
- run `uv sync`
- run `uv run pytest`
- register fake sessions
- update them
- filter waiting
- archive and restore
- prune stale
- optionally open one session

### Exit criteria
- MVP feels coherent and demoable

---

# Testing strategy

Testing should happen continuously, not at the end.

## 1. Unit tests
Focus on deterministic logic:
- data models
- state transitions
- db access
- filtering
- stale derivation
- event ingestion
- tmux command generation
- environment parsing

## 2. CLI tests
Use subprocess or CLI runner tests to verify:
- command wiring
- human-readable output
- error handling
- argument parsing

## 3. Fixture-based ingest tests
Create sample event payload files and replay them through ingest:
- started
- heartbeat
- waiting
- blocked
- done
- malformed

This is critical because the ingest path is the bridge between Claude hooks and the registry.

## 4. Manual shell verification
Do not skip this.
The point of the tool is to improve real workflows. Test in actual terminal conditions:
- tmux and non-tmux
- git and non-git
- remote shell
- ad hoc sessions
- managed tasks

## 5. Database inspection
Occasionally inspect the SQLite DB directly:
- ensure rows are sensible
- verify timestamps
- confirm archive and stale behavior

## 6. Packaging verification
Test both supported modes:
- development mode via `uv sync` and `uv run`
- tool mode via `uv tool install .`

The CLI should behave consistently in both.

---

# Verification matrix

## Registration
- manual registration works
- unknown session via ingest auto-registers
- partial metadata does not break registration

## State
- waiting sessions appear correctly
- stale sessions are derived correctly
- archive hides sessions from active views

## Identity
- sessions in different directories do not collide unnecessarily
- sessions in tmux carry useful metadata
- sessions outside tmux still remain trackable

## CLI UX
- outputs are readable
- errors are useful
- common flows are short

## Persistence
- sessions remain after restart
- events are preserved
- archived data is not lost

## Safety
- prune does not silently destroy useful information
- malformed events fail cleanly
- missing optional metadata does not crash the system

## Packaging
- `agi` is available through `uv run agi`
- `agi` is available after `uv tool install .`
- script entry point is stable and documented

---

# Suggested development order for Claude

Ask Claude to build in this exact order:

1. bootstrap project and tests
2. uv-compatible packaging and script entry point
3. SQLite schema and registry
4. states and filtering
5. CLI list, waiting, show, rename, archive
6. environment detection
7. ingest event pipeline
8. task wrapper commands
9. tmux jump support
10. stale and prune
11. optional worktree helpers

Do not ask it to build everything at once.

---

# Definition of done for MVP

The MVP is done when all of the following are true:

- I can register sessions manually
- I can ingest session events from structured input
- unknown sessions auto-register through ingest
- I can list active sessions
- I can list waiting sessions
- I can rename and archive sessions
- stale sessions can be identified
- the database is inspectable and coherent
- the code has tests for the main workflows
- the system remains useful without tmux, git, or worktrees
- the package works with uv for both development and tool installation

---

# Risks and guardrails

## Risk: overbuilding
This project can easily spiral into a platform. Resist that.

## Risk: too much abstraction
Prefer plain functions and small modules over a big framework.

## Risk: UI-first detour
Do not build the dashboard first. Prove the registry and CLI loop first.

## Risk: assuming perfect metadata
Sessions will often be messy. The system must tolerate partial information.

## Risk: coupling too tightly to one launch path
Support both managed and ad hoc sessions.

## Guardrails
- favor narrow slices
- test each slice before moving on
- keep the DB schema simple
- log events for debugging
- defer fancy surfaces until the CLI is obviously valuable