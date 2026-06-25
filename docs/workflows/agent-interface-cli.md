# Workflow: shipping a task in `agent-interface`

A step-by-step playbook for the recurring work in this repo: building and
fixing the `agi` control plane across its four layers — **CLI** (Typer),
**FastAPI backend**, **SSE** event streaming, and the **Next.js UI** — and
closing the loop through the orchestrator (`agi` MCP tools).

This is the path the session registry shows agents walking over and over
(`cli`, `sse`, `backend`, `fastapi`, "you have *been* assigned …"). Follow it
to go from "assigned a task" to "auto-commit lands clean" without re-deriving
the layout each time.

> Orientation first: read [`AGENTS.md`](../../AGENTS.md) for architecture and
> the subpackage boundary, and [`docs/ORCHESTRATOR.md`](../ORCHESTRATOR.md)
> before touching tasks/projects/events/MCP. This playbook is the *operational*
> loop on top of those.

---

## 0. The mental model (where things live)

| Concern | Lives in | Notes |
| --- | --- | --- |
| CLI verbs | `src/agent_interface/cli.py` (+ `orchestrator/cli.py`) | Typer. Hot-path verbs flat (`agi next`, `agi done`); admin namespaced (`agi tasks add`). |
| Session registry core | `src/agent_interface/{registry,db,models,states}.py` | SQLite at `~/.local/share/agent-interface/registry.db`. |
| Orchestrator core | `src/agent_interface/orchestrator/` | Self-contained subpackage; **hard seam** — don't import it from session code and vice versa. |
| FastAPI backend | `src/agent_interface/web/app.py`, `web/schemas.py` | `create_app()` factory; routes for `/projects`, `/tasks`, `/dispatch`. |
| SSE stream | `web/app.py` → `GET /events/stream` | `sse-starlette` `EventSourceResponse`; the UI live-updates the board from it. |
| UI | `ui/` (Next.js 15) | Talks to the API; dev server proxies `/api/*` → FastAPI (`ui/next.config.ts`). |
| Single entry point | `agi serve` | Wires FastAPI + UI together (see AGENTS.md "Dev workflow"). |

**Source-of-truth rule:** append-only event logs are truth; `status` columns on
`tasks`/`sessions` are caches. When debugging, trust the event log.

---

## 1. Pick up the task

If you were dispatched, the SessionStart hook already injected the assignment.
Otherwise:

```bash
agi next                      # claim / recommend the next ready task
agi board                     # see the kanban view for context
```

Via MCP (what agents use): `get_assignment` to read the current task, or
`claim_next` to atomically take the highest-priority ready one.

Then **label the session** so it's identifiable in the registry (5–10 words):

```bash
agi label "fix SSE reconnect in board live-update"
# or MCP: label_session(...)
```

## 2. Set up the dev environment (once per worktree)

```bash
uv sync                       # Python deps into .venv
(cd ui && npm install)        # UI deps — only if you'll touch the UI
```

> `uv` is the project tool. A stray `VIRTUAL_ENV` warning is harmless — uv uses
> the project `.venv`, not whatever shell venv is active.

## 3. Locate the layer you're changing

- **CLI verb / flag** → `cli.py`. Hot-path verbs are registered flat near the
  bottom (`app.command("next")(_orch_cli.cmd_next)`); admin groups use
  `app.add_typer(...)`. Keep the flat/namespaced split (AGENTS.md).
- **Backend endpoint / schema** → `web/app.py` (routes inside `create_app()`)
  and `web/schemas.py` (Pydantic `*Out` models). Mount order matters: API
  routes are registered *before* the static UI mount so `/projects`,
  `/tasks/...`, `/events/stream`, `/openapi.json` keep resolving to the API.
- **SSE** → the `GET /events/stream` handler. It emits new task events; the UI
  consumes it via `ui/lib/use-event-stream.ts`. If you change the event shape,
  update both ends.
- **Orchestrator behavior** (status machine, ops) → `orchestrator/core.py` +
  `orchestrator/db.py`. Respect the subpackage seam.
- **UI** → `ui/components/` and `ui/lib/`.

Don't reach across the orchestrator seam: cross-boundary reads go through the
public `agent_interface.registry` API, not raw SQL or internal imports.

## 4. Implement, with tests alongside

Tests live in `tests/`, one file per module (`test_cli.py`, `test_web.py`,
`test_orchestrator.py`, …). Use the in-memory `conn` fixture in
`tests/conftest.py` — don't touch the real registry DB in tests.

Tight inner loop while iterating (scope to the file you're editing):

```bash
uv run pytest -q tests/test_web.py
```

## 5. Verify the running surface (when relevant)

For backend/SSE/UI changes, run it, don't just trust tests.

```bash
# API only (fast; good for endpoint + SSE checks)
uv run agi serve --no-ui --reload
curl -s http://127.0.0.1:8000/openapi.json | head      # routes wired?
curl -N  http://127.0.0.1:8000/events/stream           # SSE actually streams?

# Full stack with hot reload (UI work)
uv run agi serve --dev --reload                        # FastAPI :8000 + Next :3000
# browse http://localhost:3000  (Next proxies /api/* → FastAPI)
```

Ctrl-C shuts down; the CLI forwards termination to the Next child process.

## 6. Pre-flight gate — run BEFORE you finish

The git pre-commit hook runs `ruff check src/ tests/` then `pytest -q`, and the
commit-msg hook rejects any message mentioning "claude". When you call
`agi done`, the system auto-commits your worktree — if the hook fails, the task
bounces to **review** and the user sees the error. So gate yourself first:

```bash
scripts/preflight.sh          # mirrors the pre-commit hook exactly
```

A green run here means the auto-commit will pass the hook. Forward pytest args
to scope while iterating (`scripts/preflight.sh tests/test_cli.py`), then run it
with no args for the full gate before `done`.

> Don't put "claude" (or a Claude co-author trailer) in anything that reaches
> the commit message — the commit-msg hook hard-fails on it.

## 7. Close the loop

```bash
agi progress <task> --note "…"      # milestones along the way (MCP: progress)
agi done <task> --summary "…"       # ships + triggers auto-commit (MCP: done)
```

If you hit a hard external blocker (missing creds, unreachable service, corrupt
state) use `agi block <task> --reason … --needs user|dep|resource` (MCP:
`block`) instead of finishing. Discovered follow-up work → `add_subtask`
(child) or `add_task` (peer; lands in backlog for triage). Never end a session
silently with the task still open.

---

## Quick reference

```bash
# environment
uv sync                              # Python deps
(cd ui && npm install)               # UI deps

# inner loop
uv run pytest -q tests/test_X.py     # scoped tests
uv run agi serve --no-ui --reload    # API + SSE only
uv run agi serve --dev --reload      # full stack, hot reload

# gate before done (mirrors pre-commit hook)
scripts/preflight.sh                 # ruff check src/ tests/  +  pytest -q

# orchestrator loop
agi next | agi board | agi progress … | agi done …
```

## Gotchas

- **`agi serve` mount order** — register API routes before mounting `ui/out` or
  the static export will shadow `/projects` etc. If `ui/out` is missing, serve
  warns and runs API-only; rebuild with `(cd ui && npm run build)`.
- **Orchestrator seam** — session code must not import `orchestrator/`, and
  orchestrator code must not modify session internals. Cross-boundary reads go
  through `agent_interface.registry`.
- **Status is derived** — never write a task `status` directly; emit the typed
  event (`progress`/`block`/`done`) and let the status materialize.
- **`.worktrees/` churn** — dispatched tasks run in git worktrees; the Next dev
  watcher ignores `.worktrees` to avoid `.next` corruption (see commit history).
- **The "claude" trap** — the commit-msg hook rejects the word anywhere in the
  message, including co-author trailers. Keep auto-commit messages clean.
