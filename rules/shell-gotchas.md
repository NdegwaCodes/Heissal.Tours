# Shell & environment gotchas

Only quirks that have already cost a debugging session on this project. Do not pre-write
entries for problems not yet hit.

## Windows / this machine

- Primary shell is **PowerShell**; a Bash tool is also available. They are different
  syntaxes — `&&`, `2>/dev/null`, `head`, `which`, `export` are Bash-only. Don't mix.
- Repo lives on `H:\Websites\Heissal.Tours`. Use absolute paths in commands.
- `uv` and a Docker daemon are not always available locally; the Makefile targets assume
  both. Fall back to a plain venv + local services (below).

## Git worktrees — use absolute `-C`

Worktrees live under `.claude/worktrees/`. Always run `git -C <absolute-worktree-path>`.
Never `git -C ../..` — the Bash cwd resets between turns, so a relative `-C` escapes into the
main repo (usually a stale branch) and has already operated on the wrong repo once.

## Running the API test suite locally

- Create the venv with **Python 3.11** (`C:\Users\Dell\AppData\Local\Programs\Python\Python311\python.exe`).
  The default `python` is 3.14 and some wheels lag.
- `pip install -e .` fails: `apps/api` is a uv app with `package = false`, so package
  auto-discovery errors. Install the `pyproject.toml` dependency list explicitly.
- `apps/api/.env` must exist for pydantic-settings; env-var overrides also work.
- **Never run the suite against the real database.** Create a throwaway DB, `alembic upgrade
  head`, `python -m app.db.seed`, run pytest, drop it. Where the real DB currently lives and
  which throwaway pattern applies is a volatile fact — check memory, don't assume.
- The suite against a cloud DB is slow (minutes); run it backgrounded.
- Local Redis on this machine is **3.0.504** (old Windows port) and lacks the RESP3 `HELLO`
  command, so the health/refresh-rotation tests fail here. Infra-only, not a code defect —
  they pass on Redis 6+ (CI uses redis:7).

## Alembic

`--autogenerate` silently drops some constructs (notably inline `use_alter` for circular
FKs — the `quotes.current_version_id` ↔ `quote_versions` pair had to be added post-table by
hand). Always run `alembic check` and read the generated migration before committing it.
