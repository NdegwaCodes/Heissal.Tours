# Heissal Tours & Travel — Build Roadmap

**Owner:** IAN
**Sequence approved by the client. Build strictly in this order — core engine first, public website late.**
**Persistence:** GitHub repo — slug `heissal-tours-and-travel-platform`, display name "Heissal Tours & Travel Platform" (pending creation). Cloud sessions are ephemeral; the repo + this project are the durable record.

Legend: ☐ not started · ◑ in progress · ☑ done & verified

---

## Stage 1 — Foundation  ◑ (design ☑ signed off; repo connected; building)

**Repo:** `https://github.com/NdegwaCodes/Heissal.Tours` · working branch `feature/stage-1-foundation`.
**Decision:** fresh start — original tours-marketplace backend archived read-only under `legacy/` (pushed 2026-08-13).
**Push mechanism:** session git proxy only allows pushes to repos added to the session's *authorized sources*. Personal access tokens are NOT the mechanism (proxy injects its own credential; persistent proxy-bypass is blocked by the security classifier). ACTION NEEDED: user adds `NdegwaCodes/Heissal.Tours` to the session's sources in the Claude desktop app; then plain `git push` works. Until then, deliver progress as zip backups.

- ☐ Monorepo
- ☐ Docker
- ☐ PostgreSQL
- ☐ FastAPI
- ☐ Next.js
- ☐ Authentication / RBAC
- ☐ Database migrations
- ☐ Admin shell

Design doc: `design/stage-1-foundation.md`. Acceptance criteria + milestones (1.1–1.8) defined there.

## Stage 2 — Quote Engine  ☐
- Client · Traveller · Destination · Accommodation · Activities · Vehicles
- Pricing rules · Seasonal rates · Park fees · Quote calculation

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
