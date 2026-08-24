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

## Stage 3 — Quotation Document  ◑ (unblocked 2026-08-24; design doc written, not yet built)
Design doc: `docs/stage-3-quotation-document.md` — confirmed rules, data model, ingestion
pipeline, template map, open questions. **Read that, not this summary.**

Reference template: `HFC Bank Diani Quotation.pdf` (25-pax corporate coastal retreat,
six accommodation options) — supplied by the client and now the target output.

- ☑ 3.1 Schema + migration `03e1ff30d1ad` — 11 new tables (quote options, version options, rejected candidates, transport segments, destination transport modes, transfer rates, supplier documents + extractions, property/destination images, activity price tiers) and column additions for rate provenance, child policy, group pax, validity and the selected option. Upgrade/downgrade round-trip verified, `alembic check` clean, NOT NULL backfill tested on a populated table.
- ☑ 3.1b Schema corrections forced by the real rate corpus, migration `316e59973b79` — `occupancy` added to `accommodation_rates` and to its uniqueness key (26 of 32 real sheets price per occupancy and previously collided), new `accommodation_supplements` table for festive loadings and compulsory gala dinners (20 and 8 sheets respectively), and the contract USD→KES 130 rate seeded as data instead of existing only inside tests. Also fixed a latent FX tie-break bug that made pricing non-deterministic when two rates shared a date. Round-trip verified, `alembic check` clean, 7 new tests asserting real sheet figures.
- ☑ 3.2 Supplier-document ingestion — content-addressed upload storage, a `RateExtractionProvider` seam, a deterministic pdfplumber grid parser, and a confirm step that is the only path able to create a rate. RBAC split so confirming is its own permission. 90 new tests. Two readers cover two layout families, chosen by result: **8** of the 35 real documents are fully readable (761 rows), 12 partial (occupancy/dates missing), 12 unrecognised, 3 are text-free scans needing a vision provider. Shared `defaults` on confirm make partial sheets usable in one action — see design doc §5b-5d
- ☑ 3.3 Option pricing — cheapest-within-hotel, meal-plan fallback chain, ceil(pax/capacity) rooming with full-price single, half-discount pass-through, 5% contingency + fixed 24% profit, per-occupancy rate selection (§3.3), minimum-stay refusal that still appears on the document as a missed-out option (§3.3a), and mandatory supplements (§3.5a). Pure rules in `quotes/options.py`, lookups in `quotes/option_pricing.py`, exposed as `POST /quotes/{id}/options/price` with a two-schema internal/client split. Rates are selected **per night** so a stay crossing into the festive window is not priced at the cheaper season. 49 new tests; every figure worked by hand. Corrected §3.3's single-supplement fallback (incoherent on per-room rates) and fixed the occupancy tiebreak 3.1b left in the Stage 2 lookup
- ☑ 3.4 Multi-option quote assembly — option add/edit/remove, exactly-one recommendation, agent-typed rejected candidates that survive a re-price (migration `15c4d3d4af6b` adds `source`), graded readiness (blocking vs advisory) at `GET /quotes/{id}/readiness`, and `POST /quotes/{id}/issue` freezing an immutable version with per-option figures, a 30-day validity stamped at issue and its own `quote:issue` permission. "BnB option" is defined as one whose resolved plan needs a chef, not by a category label (§3.7a). Also exposed the Stage 3 pricing config (profit, contingency, rounding, option bounds) — present on the model since 3.3 but missing from the API schemas, so configurable in name only, and fixed an unordered `LIMIT` in the shared CRUD listing that could hide a just-created row. 30 new tests
- ☐ 3.5 HTML template matching the sample
- ☐ 3.6 PDF generation + quote number + validity
- ☐ 3.7 Correctness tests — the sample reproduced end to end

Key rules (detail in the design doc): rates are stored **VAT-inclusive** (16%; exclusive
sources normalised ×1.16) so the engine adds no tax on top; margin, contingency and
supplier/STO rates are **backend only**; per-person is rounded up to the nearest 100 and
the group total derived from it so the two always reconcile.

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
2. ~~Sample quotation document~~ ✅ received 2026-08-24 (`HFC Bank Diani Quotation.pdf`); rules confirmed in `docs/stage-3-quotation-document.md`. Still needed from the client: **the template's font files** (must be used unaltered), the profit-% default within 20–25%, and the chef-cost figure for the bed-and-breakfast fallback.
3. ~~Stage 1 defaults~~ ✅ resolved: residency-driven pricing tier + default presentation currency (KES/USD/EUR/GBP, overridable), `Africa/Nairobi` timezone, create `web`+`portal` stubs now, repo slug `heissal-tours-and-travel-platform`.
