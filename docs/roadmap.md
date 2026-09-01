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

Data note: **rate intake template now exists** — `docs/templates/rate-intake/hotel-rates.csv`,
one sheet, one row per price, plus a guide naming the five expensive mistakes. It is the manual
path for the 27 of 35 supplier sheets the parser cannot fully read. Started as four normalised
files and was collapsed to one on client feedback: keeping four files in step by name is join
work done by hand, and repeating a property name down five rows is both less effort and less
error-prone. The **importer that consumes it is not built yet**. Two intake-only columns it must
honour: `row_type` (`RATE` / `SUPPLEMENT` / `EXTRA`, which is how a compulsory charge is told
from an optional one) and `charged_per` (`room_per_night` / `person_per_night` /
`person_per_stay` / `room_per_stay`) — our rates are stored per room and 3 real sheets are
quoted per person sharing, so the conversion has to happen on import where it happens once.

## Stage 3 — Quotation Document  ☑ (3.1–3.7 built, tested and verified against the reference proposal)
Design doc: `docs/stage-3-quotation-document.md` — confirmed rules, data model, ingestion
pipeline, template map, open questions. **Read that, not this summary.**

Reference template: `HFC Bank Diani Quotation.pdf` (25-pax corporate coastal retreat,
six accommodation options) — supplied by the client and now the target output.

- ☑ 3.1 Schema + migration `03e1ff30d1ad` — 11 new tables (quote options, version options, rejected candidates, transport segments, destination transport modes, transfer rates, supplier documents + extractions, property/destination images, activity price tiers) and column additions for rate provenance, child policy, group pax, validity and the selected option. Upgrade/downgrade round-trip verified, `alembic check` clean, NOT NULL backfill tested on a populated table.
- ☑ 3.1b Schema corrections forced by the real rate corpus, migration `316e59973b79` — `occupancy` added to `accommodation_rates` and to its uniqueness key (26 of 32 real sheets price per occupancy and previously collided), new `accommodation_supplements` table for festive loadings and compulsory gala dinners (20 and 8 sheets respectively), and the contract USD→KES 130 rate seeded as data instead of existing only inside tests. Also fixed a latent FX tie-break bug that made pricing non-deterministic when two rates shared a date. Round-trip verified, `alembic check` clean, 7 new tests asserting real sheet figures.
- ☑ 3.2 Supplier-document ingestion — content-addressed upload storage, a `RateExtractionProvider` seam, a deterministic pdfplumber grid parser, and a confirm step that is the only path able to create a rate. RBAC split so confirming is its own permission. 90 new tests. Two readers cover two layout families, chosen by result: **8** of the 35 real documents are fully readable (761 rows), 12 partial (occupancy/dates missing), 12 unrecognised, 3 are text-free scans needing a vision provider. Shared `defaults` on confirm make partial sheets usable in one action — see design doc §5b-5d
- ☑ 3.3 Option pricing — cheapest-within-hotel, meal-plan fallback chain, ceil(pax/capacity) rooming with full-price single, half-discount pass-through, 5% contingency + fixed 24% profit, per-occupancy rate selection (§3.3), minimum-stay refusal that still appears on the document as a missed-out option (§3.3a), and mandatory supplements (§3.5a). Pure rules in `quotes/options.py`, lookups in `quotes/option_pricing.py`, exposed as `POST /quotes/{id}/options/price` with a two-schema internal/client split. Rates are selected **per night** so a stay crossing into the festive window is not priced at the cheaper season. 49 new tests; every figure worked by hand. Corrected §3.3's single-supplement fallback (incoherent on per-room rates) and fixed the occupancy tiebreak 3.1b left in the Stage 2 lookup
- ☑ 3.4 Multi-option quote assembly — option add/edit/remove, exactly-one recommendation, agent-typed rejected candidates that survive a re-price (migration `15c4d3d4af6b` adds `source`), graded readiness (blocking vs advisory) at `GET /quotes/{id}/readiness`, and `POST /quotes/{id}/issue` freezing an immutable version with per-option figures, a 30-day validity stamped at issue and its own `quote:issue` permission. "BnB option" is defined as one whose resolved plan needs a chef, not by a category label (§3.7a). Also exposed the Stage 3 pricing config (profit, contingency, rounding, option bounds) — present on the model since 3.3 but missing from the API schemas, so configurable in name only, and fixed an unordered `LIMIT` in the shared CRUD listing that could hide a just-created row. 30 new tests
- ☑ 3.5 HTML template matching the sample — 9 sections rendered from the frozen version at `GET /quotes/{id}/document.html` (an unissued quote deliberately has no document). New `documents` module: a client-safe `QuotationView` with no cost field at all, a Jinja template whose type is reached through exactly two CSS custom properties, and `app_settings["document"]` holding the brand copy, notices, VAT note and page size. New `media` module giving the 3.1 image tables a service: content-addressed upload, one-hero-per-property, permission-checked delivery. Migration `072fce0720a1` adds the per-quote cover copy. Verified visually against the reference proposal by printing to PDF and reading the pages back, which caught three layout flaws. 23 new tests, including a leak check against the rendered bytes
- ☑ 3.11 Brand typography — Cormorant Garamond (display) and Libre Franklin (body), supplied by the client 2026-08-25, closing the design doc's first open question. Three variable woff2 committed under `app/modules/documents/fonts/` and **embedded as data URIs**, not linked: the PDF path renders a local `file://` page, so an unresolvable font request yields a proposal in a fallback face at different metrics with nothing raised. Five listed Cormorant weights downloaded as five identical files — both families are variable, so it is 3 files and 112 KB rather than 9 and 302 KB. The client's full px type scale applied as `--fs-*` tokens (× 0.75; body 14px → 10.5pt is what identifies the 96dpi A4 artboard). Swapping the placeholders was a two-line `DocumentConfig` edit because 3.5 forbade the template from naming a font anywhere else. The larger display type orphaned the footer onto three sheets; space came back from imagery and over-loose display leading, not from the client's sizes. 16 new tests. See docs §13
- ☑ 3.6 PDF generation + quote number + validity — `GET /quotes/{id}/document.pdf` through a `PdfRenderProvider` seam with headless Chromium behind it, filename `HTQ-YYYY-NNNN-vN.pdf`, `?version=` and `?download=` supported. Photographs are inlined as data URIs in both the PDF and the HTML — the 3.5 gap was wider than a PDF problem, since a browser will not replay a bearer token for an `<img>` either. Not cached, and the reason is written down. A missing renderer and a broken one give different, actionable errors; the HTML document keeps working without a browser. Also added image decoding on upload, after a printed cover turned out to be alt text over a dark rectangle. 15 new tests, the strongest reading text back out of the produced PDF to prove no internal figure survives view model, template and browser
- ☑ 3.7 Correctness tests — the reference proposal reproduced end to end, plus the four rules the design doc names for this milestone: VAT normalisation, discount halving, rooming edge cases and the meal-plan fallback chain. Sweeps for the invariants (every percentage from 0 to 100, every pax/capacity pair to 40 guests), hand-worked figures for the behaviour. **Found and fixed the VAT rule being documented but never implemented** — a sheet marked VAT-exclusive was stored as typed and nothing downstream ever grossed it up, so every quote off such a sheet under-charged by 16% while the document told the client the price included it. Normalisation now happens once at ingestion (`app/core/vat.py`), through both doors a rate can arrive by. Also exposed the Stage-3 rate fields the manual create/read schemas were missing — occupancy above all, which is part of a rate's uniqueness key, so a hand-entered property could hold only one rate per room/plan/season and could never be priced for a lone guest. Fixed a deduplicated image upload silently discarding its own flags, and a fixture whose fixed bytes made the suite pass only on a freshly created database. 87 new tests; 1430 passing

## Stage 3B — Multi-destination, cohorts and transport  ◑ (scoped with the client 2026-08-25)
Design doc: `docs/stage-3-quotation-document.md` §3.6b onwards. Agreed after Stage 3 shipped,
when it became clear the single-headcount / single-residency / single-destination model does
not describe real bookings.

Confirmed rules: non-residents are charged in **USD** and residents in **KES**, with the group
total converted at a **disclosed** contract rate; **flights are named but never priced** while
airport transfers are charged normally; **rooms split by residency, charges by residency and
traveller type**; meal plan is chosen **per leg** (a day excursion means half board is the
right plan, not a fallback); packages are **curated, not enumerated** — 3 legs x 4 hotels x 3
transport modes is 192 combinations and a matrix of those is meaningless; the build-up stays
backend-only and the client sees per-person and group totals only.

- ◑ 3.8 Cohorts, currencies and the cost-basis vector
  - ☑ **Pure layer** (`app/modules/quotes/cohorts.py`, no DB, sub-second): the group as
    cohorts, eight cost bases resolved against it, per-residency rooming, per-cohort build-up
    with per-person re-derived so every figure reconciles, shared costs split-then-converted
  - ☑ **Real fee data** (`app/db/seed_park_fees.py`, 15 tests asserting published figures) —
    the KWS Conservation Fees 2025 schedule (client-supplied 2026-08-25) across 18 parks,
    reserves, sanctuaries and marine parks, plus the Maasai Mara's two seasons. Added the
    missing `african_citizen` residence category and corrected `resident`; made the seeder able
    to **correct** a wrong row rather than skip it forever
  - ☐ **Remaining:** the cohort schema, wiring `OptionPricingService` onto the vector, and
    bringing park fees, mandatory activities and child rates into a price for the first time
- ☑ 3.12 Rate importer (`app/modules/rate_intake/`, 50 tests) — reads a filled-in intake sheet
  from .xlsx or .csv, normalises it, and writes destinations, properties, room types, rates and
  supplements with a two-pass dry-run/commit split. Verified against the client's real 3,161-row
  workbook (2026-08-29): **2,961 rows accepted, 2,170 rates, 30 properties, 11 destinations**,
  loaded into a throwaway `tours_intake_test` and priced end to end.
  - The importer's most valuable behaviour: **649 room-nights arrive as a rack row plus its
    agent-NETT twin**, which §3.5 models as one row carrying a derived discount. Keeping either
    row alone loses real money in opposite directions — see decision log
  - Date order is decided from the file, not assumed; a sheet mixing both orders imports nothing
  - 200 rows rejected rather than defaulted (missing validity window, occupancy, room type),
    concentrated in three properties, and reported **by property** because a row number is not
    actionable
  - Filled-in workbooks are gitignored: the blank template is tracked, typed supplier rates
    never enter git history
  - **Second workbook, audited by the client (2026-09-02, 3,190 rows).** Re-imported: **3,044
    accepted, 2,276 rates, 32 properties, 655 pairs merged, 2 conflicts, 146 rejected.** The
    client's own fix pass closed 699 of the 1,002 rack rows with no stated operator discount and
    filled every blank room type and supplement label. Three findings needed code:
    - **Blank `row_type`** on all 64 Temple Point rows. The write pass defaulted it to `RATE`,
      the capacity pass did not, so a property's rates imported while its room capacities were
      inferred from an empty set. `N.row_kind()` now decides it in one place
    - **One room-night published in three currencies** (Kobe Suite: KES/USD/EUR — 30 groups, 90
      rows) was a collision under the old key, resolved by spreadsheet row order, and the
      survivor could be the EUR figure with no rate on file. `currency` is now part of the rate
      uniqueness key (migration `8c1d2a9b4e37`), which also lets the engine prefer the
      presentation currency and skip an FX conversion. Conflicts fell 41 → 2
    - **Day-of-week pricing** (One Stop Nanyuki, Soames — 9 groups) is a distinction the schema
      has no column for. Not a false positive: the higher figure is kept and reported, so a
      weeknight over-quotes visibly rather than a weekend under-quoting 35% silently
  - **Open with the client:** `vat` is blank on 1,335 rows (45% of the corpus) and the client's
    audit says explicitly *not* to read blank as inclusive — which is what the importer does.
    Needs a third state on the rate, surfaced at quote time, not a different silent default;
    146 rows still need dates or occupancy; `EUR` appears on 30 rows with no EUR→KES rate on
    file; Peaks Hotel Nanyuki is a 2025 card extended to end-2026 and nothing covers 2027;
    24 of 36 properties price non-residents only and 25 of 36 publish a single meal plan.
    **`Mombasa/Nyali` is not a misspelling** — the
    client confirmed (2026-09-01) it means the property serves *both* destinations, which
    `Accommodation.destination_id` (one non-nullable FK) cannot express; needs a
    property×destination join, with the itinerary leg deciding which destination's park and
    conservation fees apply. Scoped into 3.9 with legs
  - **Not yet loaded into the live catalogue** — awaiting the go-ahead
  - **Open with the client:** the KWS MICE group-discount ladder is ambiguous and is shipped
    switched off (see decision log); Mara conservancy fees are often already inside a lodge
    rate, which is a double-charge risk once conservancies are priced
- ☐ 3.9 Packages — one property per leg, meal plan per leg with the agent's choice separated
  from the engine's result, leg-date contiguity as a blocking check, per-leg minimum stay
- ☐ 3.10 Transport pricing — per transition plus arrival and departure, flights named and
  unpriced, airport transfers charged, transfers validated per line-haul
- ☐ 3.11 The curated package x transport table
- ☐ 3.12 Internal costing worksheet (the mirror of the client document, every line with its
  basis, multiplier and source document) + the exclusions list (travel insurance and the rest)

**Known gap carried in:** `compute_park_fee` has no callers anywhere — park fees are computed
nowhere in the system, despite a docstring claiming the Stage 2.8 engine uses them. 3.8 closes
this.

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
