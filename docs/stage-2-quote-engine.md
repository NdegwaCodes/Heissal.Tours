# Heissal Platform — Stage 2: Quote Engine Design

**Status:** Draft for build
**Date:** 2026-08-13
**Scope:** Clients, travellers, destinations, accommodation + rates, park fees, activities, vehicles + fuel, pricing rules, seasonal rates, and the **PricingEngine** that calculates a quote.
**Builds on:** Stage 1 foundation (modular monolith, async SQLAlchemy, RBAC, audit, money=`Numeric`/`Decimal`+currency, UUIDv7, `TIMESTAMPTZ`).

---

## 1. Guiding rules (from the master prompt)

- **Nothing hard-coded.** Park fees, hotel rates, fuel prices, FX, markups, taxes, child-age limits, vehicle consumption — all come from DB rows the admin controls.
- **Residence category drives the rate tier**, and (separately) a default presentation currency. The two are decoupled (see Stage 1 §16).
- **Internal cost vs. selling price** is sacred: the client never sees internal cost or margin. Enforced at the response-schema level (separate client vs. internal serializers).
- **Money is `Decimal` + currency.** Every stored amount carries an ISO-4217 currency code. Each cost line is computed in its **source** currency and converted to the quote's **presentation** currency via the ExchangeRate service.
- **Quotes are versioned**, never silently overwritten (`HTQ-YYYY-NNNNN` + version).
- **Pricing lives in a dedicated `PricingEngine` service**, not in the quote API endpoint and never in the frontend.

---

## 2. Domain overview (new tables in Stage 2)

Reference / configuration:
```
residence_categories   citizen / EA resident / resident / non-resident … (configurable, editable)
currencies             ISO-4217 codes the business trades in
exchange_rates         admin-set FX rates, effective-dated (ExchangeRate service reads latest ≤ date)
suppliers              lodges/DMCs/activity/transport providers (light record; full mgmt in Stage 8)
destinations           cities, parks, conservancies, hubs (structured; parks carry fees)
```

Catalogue + rates:
```
accommodations         property (destination, supplier, category, star, geo, check-in/out, images)
room_types             per property (single/double/twin/triple/family/child, max occupancy)
meal_plans             RO/BB/HB/FB/AI reference
accommodation_rates    per (property, room_type, meal_plan, residence_category, season/date-range):
                       rate_per_night, child_rate, single_supplement, currency, min_nights
park_fees              per (destination, fee_type, residence_category): adult/child/infant,
                       currency, effective dates, child-age bounds
activities             catalogue (destination, supplier, duration, optional?)
activity_rates         per (activity, residence_category): adult_price, child_price, currency, dates
vehicles               capacity, fuel_type, consumption (km/L), cost_per_km, daily_operating_cost,
                       driver_cost_per_day, currency
fuel_prices            per fuel_type: price_per_litre, currency, effective_from (history)
pricing_settings       markup default %, tax %, quote validity days … (uses app_settings + overrides)
```

Quote:
```
clients                name, email, phone, country, nationality, residence_category (default)
quotes                 quote_number, client, status, presentation_currency, residence_category,
                       arrival/departure, markup/discount/tax (overridable), current_version
quote_versions         version_number, immutable snapshot (jsonb) + totals (internal_cost,
                       selling_price, gross_profit, gross_margin, currency), created_by
quote_travellers       type (adult/child/infant), age  (per-child age captured)
quote_legs             ordered destinations with nights, check_in/check_out
quote_accommodations   selection: leg, accommodation, room_type, meal_plan, rooms, nights
quote_activities       selection: leg/day, activity, pax
quote_transport        vehicle, estimated_km, days
quote_items            computed line items (internal cost + client price) per version, by category
```

> Sequencing: reference data → catalogue+rates → quote assembly → PricingEngine. Each module ships models → migration → service → API → tests before the next.

---

## 3. Rate selection (the correctness core)

Given a requirement, the engine selects exactly one rate row deterministically:

**Accommodation:** `accommodation_rates` where `accommodation_id`, `room_type_id`, `meal_plan_id`, `residence_category_id` match and the stay date falls in `[effective_from, effective_to]`. If multiple match, the most specific / latest `effective_from` wins; ties or gaps raise a **explicit “no rate found”** error rather than guessing (never assume a price). Nights × rooms × `rate_per_night`; child travellers use `child_rate` when present; `single_supplement` applied when a room is single-occupied.

**Park fees:** `park_fees` for the destination + residence_category, date in range. Traveller classification uses the fee row's own `child_min_age`/`child_max_age` (parks differ), falling back to a global default from settings only if the row leaves them null. Fee × days × pax-in-band (adult/child/infant).

**Activities:** `activity_rates` for activity + residence_category + date; `adult_price×adults + child_price×children`.

**Transport:** `fuel_litres = km / vehicle.consumption_kmpl`; `fuel_cost = fuel_litres × fuel_price(fuel_type, date)`; `transport_internal = fuel_cost + driver_cost_per_day×days + daily_operating_cost×days`. (A separate game-drive/terrain consumption factor is a later refinement; the seam is a per-leg consumption multiplier defaulting to 1.0.)

Every selection failure is surfaced (“no accommodation rate for these dates/room/plan”), never silently zeroed.

---

## 4. PricingEngine algorithm

Input: a structured `QuoteRequest` (residence_category, travellers with ages, dates, legs with accommodation/activity selections, transport, markup/discount/tax overrides, presentation_currency). Pure function over data — no hidden state.

```
1. Classify travellers (adult/child/infant) — per-rule age bounds where provided.
2. For each leg: accommodation lines, park-fee lines, activity lines  → each with source currency.
3. Transport lines (fuel + driver + operating).
4. Convert every line to the presentation currency (ExchangeRate service, quote date).
5. internal_cost = Σ line internal costs.
6. selling_subtotal = internal_cost × (1 + markup%)  [or + fixed markup].
7. after_discount = selling_subtotal − discount (percentage or fixed; threshold → needs approval).
8. tax = after_discount × tax%.
9. selling_price = after_discount + tax.
10. gross_profit = selling_price − internal_cost;  gross_margin = gross_profit / selling_price.
Return: line breakdown (internal cost + client price per line), subtotals, totals, currency.
```

Invariants asserted in tests: `selling_price ≥ 0`, `internal_cost ≥ 0`, `gross_profit = selling − internal`, `gross_margin = profit / selling` (selling>0), counts ≥ 0, `departure > arrival`.

`POST /api/v1/quotes/calculate` runs the engine on a request **without persisting** (live quote builder). Saving a quote persists inputs + an immutable `quote_version` snapshot of the computed breakdown.

---

## 5. Internal cost vs. client price

Each `quote_item` stores both `internal_cost` and `unit_price` (client). Two serializers:
- **Internal** (staff with `quote:read_cost`): full breakdown incl. cost, markup, margin.
- **Client** (PDF / portal): line descriptions + client prices + totals only. Cost/margin fields are never included in the client schema — not merely hidden in the UI.

---

## 6. Currency & FX

`ExchangeRateProvider` interface (Stage 1 seam) gets its first concrete impl: `AdminExchangeRateProvider` reads `exchange_rates` for the latest rate with `effective_from ≤ date`. `convert(amount, from_ccy, to_ccy, date)` returns a `Decimal`; identity when equal; raises if no rate path exists (never assume 1:1). A live-API provider can replace it later without touching the engine.

---

## 7. Quote numbering & versioning

`quote_number = HTQ-<year>-<zero-padded sequence>` from a per-year counter (table or Postgres sequence). Editing a **sent** quote creates a new `quote_version` (V2, V3…). `quote_versions` rows are immutable snapshots; `quotes.current_version_id` points at the latest. Price overrides and discounts beyond thresholds write audit rows (Stage 1 AuditService) and require the appropriate permission.

---

## 8. New permissions (Stage 2)

`client:*`, `destination:*`, `accommodation:*`, `activity:*`, `vehicle:*`, `park_fee:*`, `rate:*`, `fx:*`, `quote:create`, `quote:read`, `quote:read_cost`, `quote:price_override`, `quote:approve_discount`. Seeded and mapped onto the existing roles (sales_agent gets quote:create/read; finance/admin get cost + override; etc.).

---

## 9. Build sequence (milestones)

```
2.1  Reference data: residence_categories, currencies, exchange_rates, suppliers, destinations
2.2  Accommodations + room_types + meal_plans + accommodation_rates + rate selection
2.3  Park/conservation fees + selection
2.4  Activities + activity_rates
2.5  Vehicles + fuel_prices + transport cost
2.6  Pricing config (markup/discount/tax) + ExchangeRate service
2.7  Clients + Quote domain (quotes, versions, travellers, legs, selections)
2.8  PricingEngine + POST /quotes/calculate + persistence + versioning
2.9  Correctness/edge-case/invariant test suite; admin catalogue UI (subset)
```

Each milestone: models → migration (verified on fresh DB) → service → API (RBAC) → tests, then commit + bundle.

---

## 10. Definition of done (Stage 2)

An authorized agent can, via API: create a client; enter travellers (incl. child ages) and dates; select destinations, accommodation, activities, and a vehicle; and `POST /quotes/calculate` to get a correct cost breakdown, selling price, and margin — with every rate pulled from configurable data, correct multi-currency conversion, internal cost hidden from client output, and independently-verified pricing math. Quote save produces an immutable, versioned snapshot.
