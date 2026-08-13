#!/usr/bin/env bash
# Apply migrations and seed baseline data, then run the given command (the server).
set -euo pipefail

echo "[entrypoint] waiting for database..."
until uv run python -c "
import asyncio, sys
from sqlalchemy import text
from app.db.session import engine
async def check():
    async with engine.connect() as c:
        await c.execute(text('SELECT 1'))
asyncio.run(check())
" 2>/dev/null; do
  echo "[entrypoint] database not ready, retrying in 2s..."
  sleep 2
done

echo "[entrypoint] running migrations..."
uv run alembic upgrade head

echo "[entrypoint] seeding baseline data..."
uv run python -m app.db.seed

echo "[entrypoint] starting: $*"
exec "$@"
