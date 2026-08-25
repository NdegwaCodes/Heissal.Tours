# Heissal Tours & Travel Platform

An API-first tour **sales & operations** platform — quotation, pricing, itinerary,
CRM, bookings, payments, operations — built so one backend eventually serves the
website, admin dashboard, client portal, and a future mobile app.

> Build order is deliberate: the **Quotation + Pricing + Itinerary engine** is the
> commercial core and is built first; the public website comes last. See
> `docs/roadmap.md` and `docs/stage-1-foundation.md`.

## Monorepo layout

```
apps/
  api/        FastAPI backend — the single backend for all clients (Python, uv)
  admin/      Next.js internal admin/sales dashboard
  web/        Next.js public website          (Stage 6 — stub)
  portal/     Next.js client portal            (Stage 7 — stub)
packages/
  api-client/     TS client generated from the API's OpenAPI schema
  ui/             Shared shadcn/ui components + Tailwind preset
  config-eslint/  Shared ESLint config
  config-tsconfig/Shared tsconfig bases
infra/          Docker & compose support files
docs/           Design docs (roadmap, stage designs)
legacy/         Archived original code — reference only, not built
```

## Prerequisites

- Docker + Docker Compose
- Node ≥ 20 and pnpm 10 (`corepack enable`)
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A Chromium-family browser, to print quotations to PDF. Found automatically in the
  usual places; set `PDF_BROWSER_PATH` to pin one, and `PDF_BROWSER_NO_SANDBOX=true`
  in a container. Without it the HTML document still renders and the PDF endpoint
  explains what is missing rather than failing obscurely.

## Quick start (Docker — full stack)

```bash
cp .env.example .env        # then edit secrets
make up                     # db + redis + api + admin
# API:   http://localhost:8000/docs
# Admin: http://localhost:3000
```

The API container applies migrations and seeds the first superuser on startup.

## Quick start (backend only, local)

```bash
cp .env.example .env        # set POSTGRES_HOST=localhost, REDIS_URL=redis://localhost:6379/0
make api-install            # uv sync
make migrate                # alembic upgrade head
make seed                   # permissions, roles, superuser
make api-dev                # uvicorn on :8000
```

## Quick start (admin app, local)

With the API running on `:8000`:

```bash
pnpm install
API_BASE_URL=http://localhost:8000 pnpm --filter @heissal/admin dev   # http://localhost:3000
```

Sign in with the seeded superuser (`FIRST_SUPERUSER_EMAIL` / `FIRST_SUPERUSER_PASSWORD`).
The admin talks to the API only through its server-side BFF (`/api/proxy/*`), so JWTs
live in httpOnly cookies and never touch browser JS. (A Docker image for the admin is
added in a later pass; for now run it with pnpm as above.)

## Common commands

| Command | What it does |
| --- | --- |
| `make up` / `make down` | Start / stop the Docker stack |
| `make migrate` | Apply Alembic migrations |
| `make makemigration name="..."` | Autogenerate a migration |
| `make seed` | Seed permissions/roles/superuser |
| `make api-test` | Backend tests (pytest) |
| `make api-lint` / `make api-typecheck` | Ruff / mypy |
| `make web-dev` | Run frontend apps |

## Conventions (non-negotiable foundations)

- Business logic lives in backend **services**, never in routers or the frontend.
- Money is `NUMERIC`/`Decimal` **with a currency code** — never floats.
- Timestamps are UTC `TIMESTAMPTZ`; display timezone defaults to `Africa/Nairobi`.
- Nothing business-related is hard-coded — rates, fees, fuel, FX, markups, taxes,
  child-age rules all come from the DB / config / verified input / external API.
- Internal cost & margin are never serialized to client-facing roles.

## Status

Stage 1 (Foundation) in progress. See `docs/roadmap.md` for the live checklist.
