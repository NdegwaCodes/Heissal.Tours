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

## Pointing tests at a database — override by env var only

`Settings.model_config.env_file` reads BOTH the repo-root `.env` and `apps/api/.env`, and
pydantic-settings gives the **later** entry priority. Writing an `apps/api/.env` to redirect a
test run is therefore not reliable across a reordering of that tuple — and when the order was
`(".env", "../../.env")` the root file silently won, which pointed a "test" migrate+seed at the
hosted production DB. Always override with a real environment variable (`DATABASE_URL=...`),
which beats every dotenv file, and assert the resolved URI before running anything:

```bash
python -c "from app.core.config import settings; assert 'HeissalTours_test' in settings.sqlalchemy_sync_uri"
```

Neon specifics: `CREATE DATABASE` / `DROP DATABASE` must go to the **direct** endpoint (strip
`-pooler` from the host) because PgBouncer refuses them, and psycopg2 needs autocommit for both.

## pytest looks hung when it is only slow

Redirected stdout is block-buffered, so a piped `pytest` writes nothing until it exits — a
20-minute run looks identical to a hang. Set `PYTHONUNBUFFERED=1` to watch progress, and size
expectations against the DB: a Neon-backed API test costs **~100 seconds** (≈15 HTTP round trips
to `ap-southeast-2`), so the 10-test edge file takes ~17 minutes. Background these runs.

## Alembic

`--autogenerate` silently drops some constructs (notably inline `use_alter` for circular
FKs — the `quotes.current_version_id` ↔ `quote_versions` pair had to be added post-table by
hand). Always run `alembic check` and read the generated migration before committing it.

## Passing Python (or any code) through a shell heredoc corrupts backslash escapes

Three separate faults in one session, all from the same cause: text written into a
`python - <<'PYEOF'` heredoc is decoded by Python as a **string literal**, so any
valid Python escape sequence in it is interpreted before the code runs.

| Written | What reached the file | Effect |
|---|---|---|
| `re.compile(r"\b(20[2-3]\d)\b")` | `\x08(20[2-3]\d)\x08` | Regex required literal **backspace** characters and silently never matched |
| `re.search(r"\b(SGL\|DBL)\b", …)` | `^H(SGL\|DBL)^H` | Same — ambiguity detection quietly did nothing |
| Em dash in a doc string | `â€"` | Mojibake, because stdin was decoded as cp1252 |

`\d` survives (it is not a valid Python escape, so it passes through) which makes this
worse: the pattern *looks* fine and half of it works.

### The `\n`-in-a-replacement-string variant — three occurrences, all in `cli.py`

The worst case is a heredoc script that *edits another file*, because the escape is
resolved one level too few. Writing `"\\n"` inside the heredoc's replacement string
yields a literal newline in the target `.py`, breaking a string literal:

```python
print(f"\n-- HEADER")     # what you want in cli.py
b = 'print(f"\\n-- HEADER")'   # inside a heredoc -> writes a real newline. Broken.
```

It fails as a `SyntaxError: unterminated string literal`, so it is at least loud —
but it has cost three round trips. **Use the Edit tool for any replacement string
containing a newline escape.** Do not try to get the escaping level right.

Rules:

0. **Never use a heredoc'd script to insert code containing `\n`.** Use Edit.
1. **Write files with the Write tool**, not a heredoc, whenever the content contains
   backslashes, quotes, apostrophes or non-ASCII text.
2. If a heredoc is unavoidable, prefer escapes that have no Python meaning: use
   `(?<![0-9])…(?![0-9])` instead of `\b`, and double every backslash you actually want
   (`\b`).
3. Always run `PYTHONUTF8=1` when piping Python via stdin, or non-ASCII becomes mojibake.
4. **Verify after writing**, because these fail silently. Check the bytes, not the
   rendering:
   ```bash
   grep -n 'pattern' file.py | cat -v          # a literal ^H is a corrupted \b
   python -c "import m; print(m._RE.pattern.encode())"
   ```
   `cat -v` renders control characters; a plain `grep` or an editor will not show them.

## `pytest` on its own runs against the LIVE database

`scripts/test_local.sh` exports `DATABASE_URL` from `.env.test` and refuses any name not
ending in `_test`. Running `pytest` directly skips all of that and falls back to `.env`,
which points at the **live Neon catalogue** (`HeissalTours`). The suite seeds, mutates and
deletes rows.

This was found on 2026-09-02 by running `pytest tests/test_documents.py` to chase one
failure: 26 tests errored with `column accommodations.blurb does not exist`, because the
live database was behind on a migration. That is the only reason nothing was written.

`tests/conftest.py` now refuses it at **collection time**, before a fixture opens a
connection:

```
RuntimeError: REFUSING to run tests against database 'HeissalTours' — the name does not
end in '_test'.
```

Always `bash scripts/test_local.sh [args]`. It takes the same pytest arguments.

Related trap: a *stale row* in the shared `tours_test` database can keep an out-of-date
assertion passing indefinitely. `test_the_real_fonts_can_be_swapped_in_through_config`
asserted `fonts_are_placeholders is True` for a week after 3.11 made the real faces the
default, because its own `finally` block wrote the literal `True` back each run. It failed
the first time the database was rebuilt from migrations. **Run `RESET_DB=1
bash scripts/test_local.sh` before believing a green suite** after changing a config
default or a seeded value.
