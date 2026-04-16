# Deploy

Two supported paths. Pick one.

- [Single container on fly.io](#single-container-on-flyio) — preferred. One
  image, one machine, volume-backed SQLite. Fits inside the free allowance
  for small workloads.
- [Split deploy: Vercel (UI) + fly.io (API)](#split-deploy-vercel-ui--flyio-api) — useful
  if you want CDN-backed UI hosting and don't mind two moving pieces.

Both build the same app from the same repo. The runtime contract is: FastAPI
listens on `$PORT`, the UI talks to it via same-origin requests, and the
SQLite file lives at `$AGI_DB_PATH`.

---

## Single container on fly.io

### What ships

`Dockerfile` is a two-stage build:

1. `node:20-alpine` builds the Next.js static export (`ui/out`).
2. `python:3.11-slim` installs the Python package and copies the static
   export in. The `agi serve` command mounts `ui/out` at `/` behind the
   FastAPI routes.

At runtime the container runs:

    agi serve --host 0.0.0.0 --port $PORT --static-dir /app/ui/out

Because FastAPI registers the static mount **after** the API routes,
`/projects`, `/tasks/*`, `/events/stream`, and `/openapi.json` keep
resolving to the API; everything else falls through to the static files.

### Persistence

SQLite lives at `$AGI_DB_PATH = /data/registry.db`. `fly.toml` declares a
volume mounted at `/data` so the data survives restarts and machine
rescheduling. The volume has to exist before the first deploy.

### First-time setup

Prereqs: a fly.io account, `flyctl` installed, and you're logged in.

```sh
# 1. Pick an app name and a region. Reuse existing fly.toml.
fly launch --no-deploy --copy-config --name <your-app-name>

# 2. Create the SQLite volume in the same region you picked above.
fly volumes create agi_data --size 1 --region <region>

# 3. Build and deploy.
fly deploy
```

After this, `fly deploy` alone is enough for subsequent pushes.

### Free-tier sizing

`fly.toml` defaults to a `shared-cpu-1x` / 256 MB machine and
`auto_stop_machines = "stop"` / `min_machines_running = 0`, so the machine
halts when idle and cold-starts on the next request. For a single user /
low-traffic board this stays inside the free allowance. Bump `[[vm]]` if
you hit OOMs or want a warmer machine.

### Health check

`fly.toml` declares an HTTP check against `GET /projects` (always returns
200 with a JSON list). No additional endpoint is needed.

### Operations

```sh
fly logs                       # tail
fly ssh console                # shell into the machine
fly ssh console -C "sqlite3 /data/registry.db .tables"
fly volumes list               # confirm the volume is attached
fly scale memory 512           # bump RAM if needed
```

### Rebuilding locally

```sh
docker build -t agi .
docker run --rm -p 8080:8080 -v "$(pwd)/.agi-data:/data" agi
# open http://localhost:8080
```

---

## Split deploy: Vercel (UI) + fly.io (API)

Use this if you want Vercel's CDN in front of the UI and don't want the
UI to share a machine with the API. It costs one extra piece of config
(`AGI_API_URL`) and one extra CORS surface.

### UI on Vercel

1. Import the repo into a Vercel project. Set **Root Directory** to
   `ui/`.
2. Remove (or ignore) `output: "export"` in `ui/next.config.ts` —
   Vercel will host the regular Next.js build instead of a static
   export. `ui/next.config.ts` already declares a `/api/:path*` rewrite
   that forwards to `AGI_API_URL`, so set that as a Vercel env var:

       AGI_API_URL = https://<your-fly-app>.fly.dev

3. Deploy. Vercel sets the standard Next.js build and output.

### API on fly.io

Same as the single-container path, but run the API without the static
mount so the container stays tiny and nothing conflicts with the Vercel
origin. Override the CMD in `fly.toml` (or build a smaller API-only
image) to run:

    agi serve --host 0.0.0.0 --port $PORT --no-ui

You can skip the Next.js build stage entirely in this mode: point the
Docker build at a single-stage python-only image, or drop the
`COPY --from=ui-build` line.

### CORS

`src/agent_interface/web/app.py` already allows `*` origins with no
credentials, so the Vercel origin will work out of the box. If you
tighten that later, add the Vercel URL to the allow-list.

### When to choose split over single

- You already have Vercel wired up for other projects.
- You want preview deployments per PR for the UI.
- You care about CDN cache hits for `/`.

For most users, the single-container path is simpler and cheaper.
