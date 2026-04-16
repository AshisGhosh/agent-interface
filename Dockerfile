# syntax=docker/dockerfile:1.7
#
# Single-container image for agi: FastAPI serves the API *and* the
# pre-built Next.js static export from ui/out. SQLite data lives on a
# mounted volume at /data (see fly.toml).

# ── Stage 1: build the Next.js static export ────────────────────────────────
FROM node:20-alpine AS ui-build
WORKDIR /ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build
# Emits /ui/out (Next.js `output: "export"`).

# ── Stage 2: Python runtime that serves API + static export ─────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# Non-root user so the mounted /data volume can be chowned once.
RUN groupadd --system app && useradd --system --gid app --home /app app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY --from=ui-build /ui/out ./ui/out

RUN mkdir -p /data && chown -R app:app /app /data
USER app

ENV AGI_DB_PATH=/data/registry.db \
    PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "agi serve --host 0.0.0.0 --port ${PORT} --static-dir /app/ui/out"]
