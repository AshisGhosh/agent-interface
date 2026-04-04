# Starter Prompt

You are helping me build a small Python CLI project called Agent Conductor.

Read `README.md` and `IMPLEMENTATION_PLAN.md` and follow them closely.

## Project goal

Build a remote-first control plane for Claude Code sessions. This project should help me see active sessions, detect which ones need my input, and jump back into the correct context later.

It should support both:
- ad hoc sessions started manually
- managed sessions started through wrapper commands

The MVP is terminal-only and SQLite-backed.

## Project identity

Use the following naming consistently:
- project name: `agent-interface`
- Python package name: `agent_interface`
- CLI command: `agi`
- CLI expansion: `Agent Interface`

## Important constraints

- Keep the MVP small and inspectable
- Do not build a web UI
- Do not build auth
- Do not build a daemon unless it is truly necessary
- Do not assume git, tmux, or worktrees always exist
- Worktrees are optional, not required
- Use plain Python modules and avoid unnecessary abstraction
- Add tests alongside the code
- Prefer incremental progress over broad scaffolding
- Ask for nothing unless absolutely necessary; make reasonable implementation decisions

## Tooling preferences

- Python 3.11+
- uv-first workflow
- lightweight CLI, preferably `typer` or `argparse`
- SQLite for persistence
- `pytest` for tests
- keep dependencies minimal

## Packaging expectations

This project should work in two modes:

### 1. Development mode
Use uv project commands such as:
- `uv sync`
- `uv run agi --help`
- `uv run pytest`

### 2. Tool install mode
The CLI should also support being installed once as a tool and then used from any repo:
- `uv tool install .`

Design the project so the `agi` command works cleanly in both modes.

## What I want you to do first

Implement only the first milestone:

1. bootstrap the Python project
2. create uv-compatible packaging and script entry point
3. create the SQLite-backed session registry
4. implement manual session registration
5. implement manual session state updates
6. implement these commands:
   - `agi sessions list`
   - `agi sessions waiting`
   - `agi sessions show <session_id>`
   - `agi sessions rename <session_id> <label>`
   - `agi sessions archive <session_id>`
   - `agi sessions restore <session_id>`
   - `agi sessions register`
   - `agi sessions update-state <session_id> <state>`
7. add tests for the above

Do not implement hook ingestion, tmux integration, worktree helpers, or web UI yet unless required for clean structure.

## Expected output style

Work in small steps.

Before coding, give me:
- the proposed folder structure
- the schema design
- the CLI command structure
- the packaging approach in `pyproject.toml`
- any small implementation choices you are making

Then implement the first milestone cleanly.

## Definition of success for this step

At the end of this first pass, I should be able to:
- run `uv sync`
- run `uv run agi --help`
- run `uv run pytest`
- register fake sessions manually
- list them
- filter waiting sessions
- rename them
- archive and restore them
- inspect a coherent SQLite database

Extra credit only if it is low effort:
- confirm the CLI is also compatible with `uv tool install .`

## Extra guidance

When in doubt:
- choose simplicity
- keep the DB schema obvious
- keep command output readable in a narrow terminal
- keep the code easy to extend later for ingest events and tmux jumping
- keep installation and packaging clean from the start