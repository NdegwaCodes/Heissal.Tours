# Stage 3 — Quotation Document

**Status:** design in progress. Unblocked 2026-08-24 by the first real sample,
`HFC Bank Diani Quotation.pdf` (13 pages, 25-pax corporate coastal retreat, six
accommodation options). That document is the reference template for this stage.

This doc records the rules the client confirmed, the data model they imply, and the
questions still open. It is the single source of truth for Stage 3 — the roadmap links
here rather than repeating any of it.

---

## 1. What this stage delivers

1. **Supplier-document ingestion** — hotel rate sheets (PDF, and readable designed
   images) extracted into date-ranged rate rows, with a human confirm step.
2. **A multi-option quote** — one quote presenting 3–9 hotels plus 1–2 BnB options,
   each independently priced, one flagged *Recommended*, with rejected candidates
   recorded and shown.
3. **The document itself** — HTML template → PDF, matching the sample's structure.
4. **Margin, contingency and supplier rates that never leave the backend.**

## 2. The internal/client boundary

This is the hard rule of the stage. Nothing below the line reaches the document:

| Backend only | Shown to the client |
|---|---|
| Supplier/STO/rack rates, per-hotel cost | Final per-person rate and group total |
| Profit (24%), contingency (5%) | Inclusions list, rooming, meal plan |
| Retained half of any supplier discount | Dates, nights, destination, group size |
| Which cost line each amount came from | Option comparison table, recommendation |

The existing schema-level split (`quote:read_cost`) already enforces this for the API;
the PDF renderer must use the **client** schema, never the internal one.

## 3. Confirmed rules

### 3.1 Dates and nights

- Input is a **date range** (`arrival_date` → `departure_date`). Nights are derived,
  never typed twice.
- **Dates only — no times anywhere.** Stay/rate columns stay `DATE`, not `TIMESTAMPTZ`.
  (The UTC-`TIMESTAMPTZ` convention still applies to record metadata like `created_at`.)
- Rates are selected from the date range because hotel documents price *between two
  dates* (season windows). This is what the existing effective-dated
  `AccommodationRate` rows already model — the earlier worry about undated quotes is
  moot: a quote always has dates.
- The client document displays the dates; the night count drives the arithmetic.

### 3.2 VAT

- **Default: all stored rates are VAT-inclusive** at 16%.
- If a supplier document states its rates are VAT-*exclusive*, normalise at ingestion:
  `inclusive = exclusive × 1.16`. The stored rate is always inclusive.
- Therefore the engine adds **no tax on top** for these quotes. VAT is a disclosure
  line on the document ("All prices inclusive of 16% VAT"), not an arithmetic step.
- Every rate row carries `vat_inclusive` (bool) and `vat_pct` so the normalisation is
  auditable and the rate is never double-taxed.

### 3.3 Rooming

- `rooms_required = ceil(pax ÷ room_capacity)`. Twin-sharing is the capacity-2 case
  (25 pax → 13 rooms); Pendo's 4-guest villas are the capacity-4 case (25 → 7).
- **An odd single room is charged in full**, not half. 25 pax = 12 twins + 1 room at
  the full room rate.
- The charge is **per room**, not per person — a room accommodating 2 costs the same
  whether 1 or 2 people occupy it.

### 3.4 Meal plans

- Requested plan is picked per quote (full board or half board).
- **Fallback chain when a hotel has no rate for the requested plan:**
  `Full Board → Half Board → Bed & Breakfast + chef cost + manually entered meal cost`.
- A fallback is **flagged on the option** so the sales agent knows the plan differs
  from what was asked, and so non-comparable options can be marked (the sample already
  does this in prose for Pendo's "group meal arrangement").
- If the rate includes full board, meals are catered for — no separate meal line.

### 3.5 Supplier discounts and STO rates

- **Rack rate with a stated discount → pass half the discount to the client.**
  A document offering 15% off a 24,000 rack rate yields
  `(100 − 7.5)% × 24,000 = 22,200` as the **costed** accommodation figure.
- **The retained half-discount is NOT part of the profit percentage.** Profit is
  calculated on the whole sum, including the half-discounted accommodation figure, so
  the retained half is margin *on top of* the 24%. Three distinct numbers per rate:

  | Number | Value on the example | Where it lives |
  |---|---|---|
  | What Heissal pays the hotel | `24,000 × 0.85 = 20,400` | backend only |
  | Costed figure entering the build-up | `24,000 × 0.925 = 22,200` | backend only |
  | Retained half-discount | `1,800` | backend only, reported as margin |

- **Sell-To-Operator (STO) / tour-operator rates → use as the accommodation cost
  directly.** No rack manipulation; margin on that line is the profit % alone.

### 3.6 Margin build-up (backend only)

Cost components, all normalised to VAT-inclusive:

1. Accommodation — cheapest eligible rate **within** each hotel (§3.7)
2. Meals — only where not already included by the plan
3. Park / conservation / entrance fees — per person per day
4. **Mandatory** activities — listed with cost; appear in the document's *Included* list
5. Transport — transfers plus line-haul (§3.8)

then:

```
cost_subtotal   = sum(components)          # accommodation at the half-discounted figure
contingency     = cost_subtotal × 5%
cost_basis      = cost_subtotal + contingency
selling_total   = cost_basis × 1.24        # profit 24%, fixed
per_person      = round_up_to_100(selling_total ÷ pax)
group_total     = per_person × pax         # so the two always reconcile
```

- **Profit is a fixed 24%**, applied to the whole sum. It lives in the existing
  `app_settings["pricing"]` config (not hard-coded), with a per-quote override column
  for the exception case.
- **Contingency accrues profit** — it sits inside `cost_basis`, as modelled above.
- Realised margin on a discounted-rack option is therefore `24% + contingency + the
  retained half-discount`, which is why all three numbers are tracked separately.

Deriving `group_total` back from the rounded `per_person` is deliberate: it guarantees
the document's two headline numbers agree. The sample fails this — page 6 says
KES 28,800/person while the page 11 table says 28,400, both against a 720,000 total.

- **Per-person × pax = group total**, except a mixed booking (e.g. 1 adult + 1 child)
  where per-person is not uniform; those show the group total and a per-traveller-type
  breakdown instead.

### 3.7 Option selection

- **Cheapest within a hotel** — across that hotel's room types for the eligible meal
  plan. The system never picks *between* hotels; the client is shown 3–9 hotels plus
  1–2 BnB options and chooses.
- Options that fall back to a different meal plan, or whose structure isn't comparable
  (villa vs full-board resort), are flagged as such.
- **Rejected candidates are recorded and shown** with a reason — the sample's Diani
  Cottages entry (capacity 16 < 25 pax) demonstrates due diligence to the client.

### 3.8 Transport

Transport varies per quote and needs a segment model rather than the current
km-and-fuel vehicle costing:

| Mode | Cost basis |
|---|---|
| Own/hired vehicle (Coaster, Land Cruiser) | per vehicle per day (existing model) |
| SGR | per person per ticket, by class |
| Flight | per person per ticket |
| Transfer | per vehicle per leg |

- **Transfers are mandatory whenever line-haul is rail or air**: pickup → terminus,
  terminus → hotel, and the same in reverse. A quote using SGR or a flight without
  transfer legs is incomplete and should be rejected by validation, not silently
  under-priced.
- **VVIP transport is an optional client-facing cost**, quoted as an add-on.

### 3.9 Optional extras

- Optional activities are priced **per person** and shown separately from the package
  (the sample's Wasini page carries no price — the generated document must).
- **Timed activities need a price ladder**, e.g. quad biking 10 min / 15 min / 30 min,
  each at its own price. Stored as tiers on the activity, rendered as a small table.
- **Alcoholic beverages stay "at an additional agreed fee"** — a POA line with no price.
- Activities are saved against a location for reuse (mandatory and optional alike).

### 3.10 Geography

Hotels group by **destination**, which doubles as the geographic area: "Diani" is a
destination and every option in the sample hangs off it, while parks and reserves are
destinations of a different `type`. `Destination.region` / `country` already exist for
coarser grouping. **No schema change needed** — this is data discipline, and it keeps
"accommodations in this area" a single indexed FK lookup rather than a join through a
new table.

### 3.11 Document assembly

- Section order is **not fixed**. Some activities warrant their own full section (the
  Wasini page); that is a per-activity flag the agent sets.
- The document carries the **quote number** (`HTQ-YYYY-NNNN`, already implemented) for
  client enquiries and CRM tracking.
- **Fonts:** the exact faces aren't available yet, so the template uses close
  equivalents *as a declared placeholder* — a high-contrast display serif and a humanist
  sans. Both are referenced through **two CSS custom properties** (`--font-display`,
  `--font-body`) defined in one place, so swapping in the real faces later is a
  two-line change rather than a hunt through the template. The placeholder status is
  noted in the template header so it can't be mistaken for the final brand type.
- Property blurbs are **stored per property**, with AI paraphrasing them per quote so
  repeat clients don't receive identical copy.
- Images: as-is, **auto-cropped centrally** to the template's aspect ratios. 5–6 per
  property.

## 4. Data model changes

New tables:

| Table | Purpose |
|---|---|
| `quote_options` | One priced alternative within a quote: accommodation, resolved rate, rooming, meal plan (+ fallback flag), computed totals, `is_recommended`, `sort_order` |
| `quote_rejected_candidates` | Property considered but not offered, plus reason |
| `supplier_documents` | Uploaded source file, hotel, upload metadata, extraction status |
| `supplier_document_extractions` | Proposed rate rows from one document, pending/confirmed/rejected — the confirm step |
| `property_images` | Image per accommodation with `sort_order` and `is_hero` |
| `activity_price_tiers` | Duration/variant ladder for timed activities |
| `transport_segments` | Mode, cost basis, per-person vs per-vehicle, transfer legs |

Column additions:

- `accommodation_rates`: `vat_inclusive`, `vat_pct`, `rate_kind` (`rack` / `sto`),
  `discount_pct` (as stated by the supplier), `source_document_id`
- `activities`: `is_mandatory`, `has_own_section`
- `quotes`: `pax_count`, `profit_pct`, `contingency_pct`, `requested_meal_plan`
- `accommodations`: `blurb` (the stored per-property copy AI paraphrases)

Nothing here changes the immutable-version design: pricing a quote still appends a
`QuoteVersion` snapshot, now with its options inside the snapshot.

## 5. Ingestion pipeline (extract → confirm → store)

```
upload PDF/image
  → extract candidate rate rows (season windows, room types, meal plans, rates,
    VAT basis, rack-vs-STO, stated discounts)
  → show a diff-style confirm screen against what is already stored
  → agent approves / edits / rejects each row
  → confirmed rows become AccommodationRate records, linked to the source document
```

Extraction is **never trusted silently**. A wrong parsed money value that reaches a
client is a commercial incident, and OCR on designed images is exactly where that
happens. The source file stays attached so any rate is traceable to its document.

## 6. Image storage — decided

**Bytes on disk / object storage; metadata rows in Postgres.** Confirmed 2026-08-24.
Image data needs no transactional guarantees, and keeping 5–6 images per property out
of the database avoids inflating every backup and slowing the catalogue queries that
touch the table.

`property_images` holds `accommodation_id`, `storage_path`, `sort_order`, `is_hero`,
`width`, `height`, `content_type`, `byte_size`, `uploaded_by`, and the checksum used to
dedupe re-uploads. Files are served through the API so access stays permission-checked
rather than depending on a guessable public path. Originals are kept; the template's
aspect ratios are produced by centre-cropped derivatives generated on upload, so a
layout change can re-derive them without asking for the photos again.

## 7. CRM & analytics hook (suggestion, Stage 5)

The quote number is the join key for the whole funnel. To answer "which quotes
materialised":

- **Status lifecycle on the quote:** `draft → sent → viewed → accepted → lost`, each
  transition timestamped and appended (never overwritten), with a **lost reason**
  (price, availability, timing, competitor, no response).
- **Record which option the client chose.** This is the most valuable field in the
  system: it tells you whether your *Recommended* flag matches real behaviour, and
  whether clients systematically pick cheaper or dearer than you expect.
- **Derived metrics:** win rate by client type / destination / season / group size;
  quote-to-acceptance time; average discount given vs won; recommended-option hit rate;
  revenue per quote vs per sent quote.
- **Cohort the source:** where the enquiry came from, so marketing spend ties to
  materialised revenue.

Because versions are immutable, all of this is reconstructable historically — you can
ask "what did we quote in June and what did it become" without a data warehouse.

## 8. Resolved since the first draft

| Question | Answer (2026-08-24) |
|---|---|
| Image storage | Bytes on disk/object storage, metadata in Postgres (§6) |
| Fonts | Close equivalents as a declared placeholder until the real files arrive (§3.11) |
| Profit vs retained half-discount | Independent — profit applies to the whole sum; the retained half is margin on top (§3.5) |
| Profit percentage | **Fixed 24%**, held in pricing config with a per-quote override (§3.6) |
| Contingency placement | Inside `cost_basis`, so profit accrues on it (§3.6) |

## 9. Open questions

1. **Chef cost** for the BB fallback — a standing figure, per-hotel, or per quote?
2. **Child pricing.** The 1-adult-1-child case needs a rule: child rate from the hotel
   document where present, and what to do when absent.
3. **Document with both rack and STO rates** — always prefer STO, or take whichever is
   cheaper after the half-discount rule?
4. **Transfer cost source** — own fleet (costed by the existing vehicle model) or
   supplier-quoted per leg?
5. **SGR ticket prices and classes** — who maintains them, and do they change often
   enough to need effective dating like fuel prices?
6. **Is 16% VAT ever different** (zero-rated services, non-resident pricing)?
7. **The real font files**, when available.

## 10. Build order

- **3.1** Schema + migration for options, rejected candidates, images, price tiers,
  transport segments, rate provenance columns
- **3.2** Rate ingestion: upload → extract → confirm screen → stored rates
- **3.3** Option pricing: cheapest-within-hotel, meal fallback chain, rooming rule,
  discount/STO handling, contingency + profit, per-person rounding
- **3.4** Multi-option quote assembly + recommendation + rejected candidates
- **3.5** HTML template matching the sample, section-by-section
- **3.6** PDF generation, quote number, validity
- **3.7** Correctness tests: the sample reproduced end to end, VAT normalisation,
  discount halving, rooming edge cases (odd pax, capacity-4), fallback chain
