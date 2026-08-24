# Heissal Tours & Travel — Build Roadmap

**Owner:** Heissal
**Sequence approved by the client. Build strictly in this order — core engine first, public website late.**
**Persistence:** GitHub repo — slug `heissal-tours-and-travel-platform`, display name "Heissal Tours & Travel Platform" (pending creation). Cloud sessions are ephemeral; the repo + this project are the durable record.

Legend: ☐ not started · ◑ in progress · ☑ done & verified

---

## Stage 1 — Foundation  ◑ (everything built & verified; only Docker bring-up left)

**Repo:** `https://github.com/NdegwaCodes/Heissal.Tours` · working branch `feature/stage-1-foundation` (backs PR #1); each milestone is also fast-forwarded onto the default branch `master-heissal`.
**Decision:** fresh start — original tours-marketplace backend archived read-only under `legacy/` (pushed 2026-08-13).
**Push:** plain `git push` from the local workspace works. (The old "session git proxy blocks pushes" note applied only to the ephemeral cloud sandbox and no longer constrains anything.)

- ☑ Monorepo (pnpm/turbo + uv, shared config packages, Makefile)
- ☐ Docker — compose (db/redis/api) + Dockerfile + entrypoint written; **bring-up still unverified**. Admin image deferred; admin runs via pnpm.
- ☑ PostgreSQL (async SQLAlchemy 2.0, UUIDv7 PKs, TIMESTAMPTZ)
- ☑ FastAPI (app factory, /health, error envelope, structured logging)
- ☑ Next.js admin shell (BFF httpOnly-cookie auth + refresh rotation, RBAC-aware nav)
- ☑ Authentication / RBAC (Argon2id, JWT + refresh rotation + Redis revoke, require_permission)
- ☑ Database migrations (Alembic verified on a fresh DB + idempotent seed)
- ☑ Admin shell (login, users, roles screens)

Design doc: `docs/stage-1-foundation.md`. Acceptance criteria + milestones (1.1–1.8) defined there.

## Stage 2 — Quote Engine  ☑ (2.1–2.9 done; engine priced, tested and editable from the admin)
Design doc: `docs/stage-2-quote-engine.md` (pricing model, ERD, engine algorithm). Build order per §9.

- ☑ 2.1 Reference data — residence categories, currencies + FX, suppliers, destinations
- ☑ 2.2 Accommodations + room types + meal plans + seasonal rates + deterministic rate selection
- ☑ 2.3 Park/conservation fees — per destination/category, configurable child-age bounds
- ☑ 2.4 Activities + effective-dated per-category rates
- ☑ 2.5 Vehicles + fuel prices + transport cost (game-drive multiplier)
- ☑ 2.6 Pricing config (markup/discount/tax) in `app_settings` + ExchangeRate service
- ☑ 2.7 Clients + quote domain (quotes, versions, travellers, legs, selections)
- ☑ 2.8 PricingEngine + `POST /quotes/calculate` + immutable persistence/versioning
- ☑ 2.9 Correctness/edge/invariant tests + admin catalogue UI
  - ☑ pure invariant suite (`tests/test_pricing_invariants.py`) — 778 cases, no DB, 1.4s: breakdown identities, Decimal-not-float, discount clamping, approval threshold, age boundaries
  - ☑ engine edge suite (`tests/test_engine_edges.py`) — 10/10 green (2026-08-24): version immutability under rate drift, deterministic re-pricing, overlap tie-break, cost-leak sweep across every endpoint, age boundaries, empty quote, unknown-vehicle 404, FX line-for-line scaling
  - ☑ admin catalogue UI — destinations, accommodations, activities, vehicles (spec-driven `CatalogueResource`)
  - ☑ rates/fees editing UI — seasonal rates, park fees, activity rates, fuel prices (parent-scoped resources)

**Testing note:** DB tests run against a throwaway Neon DB (`HeissalTours_test`, created → migrated → seeded → dropped). Neon `ap-southeast-2` latency makes them slow — ~100s per test, 17 min for the 10-test edge file — so background them. Override `DATABASE_URL` by **env var**, never by editing a `.env`, and never run them against `HeissalTours`.

Dev tooling: `scripts/verify.sh` (lint + type + test); `scripts/scaffold_module.py` (CRUD modules from a JSON spec); `src/lib/catalogue.ts` is the frontend equivalent.

Data note: bulk/CSV importer for accommodations + rates still wanted once the full hotel data lands.

## Stage 3 — Quotation Document  ☐
- Map the sample quotation into the data model · HTML/document template
- PDF generation · Quote versioning · Quote sending
- *Blocked input:* sample quotation document (not yet provided).

## Stage 4 — Itinerary Engine  ☐
- Destinations · Route data · Distance/time calculation · Fuel calculation
- Route sequencing · Itinerary scoring · AI-generated narrative

## Stage 5 — CRM  ☐
- Leads · Pipeline · Follow-ups · Communications · Quote tracking · Conversion analytics

## Stage 6 — Public Website  ☐
- Packages · Destinations · Accommodation · Activities · SEO · Lead capture · Custom safari builder

## Stage 7 — Booking + Client Portal  ☐
- Quote acceptance · Booking · Payment schedules · M-Pesa/payment integration
- Client login · Documents · Online itinerary

## Stage 8 — Operations  ☐
- Vehicles · Drivers · Guides · Trip assignments · GPS integration · Fuel/mileage · Supplier management

---

## Working agreements (from the master prompt + execution loop)
- Business logic lives in backend services, never the frontend or routers.
- Nothing hard-coded: park fees, rates, fuel, FX, markups, taxes, child-age rules, vehicle consumption all come from DB / config / verified input / external API.
- Internal cost vs. selling price is a schema-level separation; clients never see cost or margin.
- Money = `NUMERIC`/`Decimal` + currency, never floats. Timestamps = UTC `TIMESTAMPTZ`.
- Quotes are versioned and never silently overwritten.
- No fake "coming soon" features; unbuilt things are labeled stubs.
- Definition of done = built AND run AND tested AND cross-checked, not "code exists."

## Pending inputs from client
1. GitHub repo created + one-time push access (unblocks all of Stage 1).
2. Sample quotation document (needed for Stage 3; useful reference earlier).
3. ~~Stage 1 defaults~~ ✅ resolved: residency-driven pricing tier + default presentation currency (KES/USD/EUR/GBP, overridable), `Africa/Nairobi` timezone, create `web`+`portal` stubs now, repo slug `heissal-tours-and-travel-platform`.
