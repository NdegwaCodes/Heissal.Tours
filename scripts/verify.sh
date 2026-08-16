#!/usr/bin/env bash
# Portable verification pipeline for the API: lint + type-check + tests.
# Run from the repo root:  bash scripts/verify.sh
# (Expects Postgres + Redis reachable per apps/api/.env, and `uv` installed.)
set -euo pipefail
cd "$(dirname "$0")/../apps/api"

echo "== ruff =="
uv run ruff check .
echo "== mypy =="
uv run mypy app
echo "== pytest =="
uv run pytest -q
echo "All checks passed."
