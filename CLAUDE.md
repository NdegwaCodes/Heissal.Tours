# Heissal Tours & Travel Platform — Claude context

API-first tour **sales & operations** platform (quotation → pricing → itinerary → CRM →
bookings → operations). One FastAPI backend serves admin, website, portal, and a future
mobile app. Build order is deliberate: the quote engine first, the public website last.

Project overview, prerequisites, and the command table live in [README.md](README.md) —
read it rather than re-deriving setup here.

## Routing — where to look for what

| Task type | Start here |
|---|---|
| What to build next, stage scope | [docs/roadmap.md](docs/roadmap.md) (live checklist) |
| Stage 1 / Stage 2 design detail | [docs/stage-1-foundation.md](docs/stage-1-foundation.md), [docs/stage-2-quote-engine.md](docs/stage-2-quote-engine.md) |
| Why a choice was made | [docs/decision-log.md](docs/decision-log.md) |
| Shell / env / tooling failures | [rules/shell-gotchas.md](rules/shell-gotchas.md) |
| Volatile facts (DB host, test env, in-flight milestone) | session memory — see *Retrieve, don't recall* below |
| A new CRUD catalogue module | [scripts/scaffold_module.py](scripts/scaffold_module.py) |

## Key directories

```
apps/api/            FastAPI backend — the ONLY backend. Python 3.11+, uv, Alembic.
  app/core/          config, deps, security, ids (UUIDv7), errors, crud base, redis
  app/db/            session, base, seed (permissions/roles/superuser)
  app/api/v1/        router assembly + health; feature routes live in modules
  app/modules/<m>/   models.py schemas.py router.py service.py  ← the unit of work
  app/integrations/  external-provider seams (e.g. exchange_rate)
  alembic/           migrations (canonical)
  tests/             pytest, one file per module
apps/admin/          Next.js admin dashboard; talks to the API only via its BFF (/api/proxy/*)
apps/web, apps/portal  stubs (Stages 6 / 7)
packages/            api-client (generated), ui, shared eslint/tsconfig
scripts/             scaffold_module.py, verify.sh (lint + typecheck + tests)
legacy/              archived original backend — REFERENCE ONLY, never built or edited
```

Stale trees at the repo root: `backend/` and `migrations/` are pre-archive leftovers from the
original marketplace app. `apps/api/` and `apps/api/alembic/` are canonical. Never edit the
root-level ones.

## Non-negotiable conventions

- Business logic lives in backend **services**. Never in routers, never in the frontend.
- Money is `NUMERIC`/`Decimal` **plus a currency code** — never floats.
- Timestamps are UTC `TIMESTAMPTZ`; display timezone defaults to `Africa/Nairobi`.
- Nothing business-related is hard-coded: park fees, rates, fuel, FX, markups, taxes,
  child-age rules, vehicle consumption all come from DB / config / verified input / API.
- Internal cost & margin are a **schema-level** split, gated by permission
  (`quote:read_cost`). Client-facing roles get a price-only view.
- New tables get a UUIDv7 PK + timestamp mixins; new CRUD gets RBAC-guarded routes and
  seeded permissions.
- Verification before claiming done: `bash scripts/verify.sh` (ruff + mypy + pytest).

## Working habits for this repo

1. **Retrieve, don't recall.** Anything that can change — where the database is hosted, how
   to run tests on this machine, which milestone is in flight, which branch to push — is
   looked up at run time (memory, `git`, `docs/roadmap.md`, `.env`), never assumed from a
   past session. A wrong recalled fact is worse than a missing one.
2. **Single source of truth.** README owns setup, roadmap owns status, decision-log owns
   rationale, this file owns routing. Write a pointer, not a copy.
3. **Fix reliability structurally.** If something is repeatedly gotten wrong, add a helper,
   a lint, a test, or a fail-safe default — not another warning sentence here.
4. **Structural over prose.** Prefer a table or a JSON spec (see the scaffolder) over a
   paragraph.
5. **Migrations are verified, not trusted.** After autogenerate, run `alembic check` and read
   the diff — autogen silently drops some constructs (see decision log).

## Not yet in this repo (add only on real pain)

`contracts/`, `skills/`, generated digests, and a multi-stage plan/verify pipeline are
deliberately absent — see [docs/context-repo-quickstart.md](docs/context-repo-quickstart.md)
for the graduation triggers. `.claude/agents/` stays empty until the same multi-step task has
been done by hand three times.
