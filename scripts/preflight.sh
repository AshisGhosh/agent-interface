#!/usr/bin/env bash
#
# preflight.sh — run the exact gate the git pre-commit hook enforces.
#
# An agent (or human) should run this *before* calling `agi done` / committing.
# It mirrors .git/hooks/pre-commit so a green run here means the auto-commit
# that fires on `done` will not bounce the task into review on a hook failure.
#
# Checks (read-only; mutates nothing):
#   1. ruff lint over src/ and tests/
#   2. the full pytest suite
#
# Usage:
#   scripts/preflight.sh                 # lint + full test suite
#   scripts/preflight.sh tests/test_web.py   # lint + only the given pytest args
#
# Extra arguments are forwarded to pytest, so you can scope the run while
# iterating and then re-run with no args for the full gate before `done`.

set -euo pipefail

# Resolve repo root from this script's location so it works from any cwd.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> ruff check src/ tests/"
uv run ruff check src/ tests/

if [ "$#" -gt 0 ]; then
    echo "==> pytest $*"
    uv run pytest -q "$@"
else
    echo "==> pytest (full suite)"
    uv run pytest -q
fi

echo "==> preflight passed — safe to commit / call done"
