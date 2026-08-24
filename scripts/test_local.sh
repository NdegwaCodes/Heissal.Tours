#!/usr/bin/env bash
# Run the API test suite against a LOCAL throwaway Postgres.
#
# Why this script exists rather than a bare pytest invocation:
#
# 1. Speed. A hosted database costs ~10-40s per DB-backed test in round trips;
#    the same tests locally are sub-second. That is the whole difference between
#    a suite you run and a suite you skip.
# 2. Safety. Credentials live in apps/api/.env.test (gitignored) instead of a
#    committed Makefile, and the guard below refuses to run against any database
#    whose name does not end in _test. Test runs write rows; pointing one at a
#    real catalogue is a data incident, and this project has already had one near
#    miss when a dotenv precedence quirk resolved a "test" run to production.
#
# Usage:
#   bash scripts/test_local.sh                     # whole suite
#   bash scripts/test_local.sh tests/test_quotes.py -q
#   RESET_DB=1 bash scripts/test_local.sh          # drop and recreate first
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="apps/api/.env.test"
PY="apps/api/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="apps/api/.venv/bin/python"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Create it with a local DATABASE_URL, e.g."
  echo "  DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/tours_test"
  exit 1
fi

# Export only the assignments, ignoring comments and blank lines.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

DB_NAME="${DATABASE_URL##*/}"
DB_NAME="${DB_NAME%%\?*}"

case "$DB_NAME" in
  *_test) ;;
  *)
    echo "REFUSING: target database '$DB_NAME' does not end in _test."
    echo "Tests write rows. Point DATABASE_URL at a throwaway database."
    exit 1
    ;;
esac

echo "== target: $DB_NAME (local) =="

if [ "${RESET_DB:-0}" = "1" ]; then
  echo "== recreating $DB_NAME =="
  "$PY" - <<PYEOF
import os, urllib.parse as up, psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
u = up.urlsplit(os.environ["DATABASE_URL"])
name = u.path.lstrip("/").split("?")[0]
assert name.endswith("_test"), name
c = psycopg2.connect(host=u.hostname, port=u.port or 5432, user=u.username,
                     password=up.unquote(u.password or ""), dbname="postgres")
c.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = c.cursor()
cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
cur.execute(f'CREATE DATABASE "{name}"')
print(f"   recreated {name}")
c.close()
PYEOF
fi

cd apps/api

echo "== migrate =="
./.venv/Scripts/python.exe -m alembic upgrade head 2>&1 | grep -E "Running upgrade|already at" | tail -3 || true

echo "== seed (reference data) =="
./.venv/Scripts/python.exe -m app.db.seed 2>&1 | tail -2

echo "== seed (demo catalogue) =="
./.venv/Scripts/python.exe -m app.db.seed_demo --yes 2>&1 | tail -2

echo "== pytest =="
if [ "$#" -gt 0 ]; then
  ./.venv/Scripts/python.exe -m pytest "$@"
else
  ./.venv/Scripts/python.exe -m pytest -q
fi
