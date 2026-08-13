# Legacy code (archived reference — do not build on)

This folder contains the **original** backend that existed in this repository before
the platform was restructured to follow the signed-off staged plan (see
`docs/` and the project's `design/` docs).

## Why it's here

The original code was a **public tours-marketplace backend** (models: `tours`,
`tour_departures`, `providers`, `reviews`, `wishlists`, `bookings`). That maps to
**Stage 6 (Public Website)** of the roadmap, not Stage 1–2. Per the master prompt,
we build the **Quotation + Pricing + Itinerary engine first**, and the public
tours website last. The original code was also not runnable at the time of
archiving (`Dockerfile` and `alembic.ini` had been deleted, and `docker-compose.yml`
referenced the missing Dockerfile).

## Status

**Reference only.** Nothing here is imported by the live application. It is kept so
that reusable pieces can be harvested later:

- `locations`, `activities`, `accommodation_types` models → useful for Stage 2 (Quote Engine) and Stage 6.
- `tours`, `tour_*`, `reviews`, `wishlists`, `providers` → useful for Stage 6 (Public Website).
- `audit_log` model → superseded by the new `audit` module in Stage 1.

When a piece is salvaged into the live codebase it will be re-implemented to match
the new conventions (RBAC, refresh tokens, `NUMERIC`/`Decimal` money, `TIMESTAMPTZ`,
created_by/updated_by, soft-delete), not copied verbatim.

Archived: 2026-08-13.
