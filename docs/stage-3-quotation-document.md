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
| **Agent cover fee** | Dates, nights, destination, group size |
| Retained half of any supplier discount | Option comparison table, recommendation |
| Chef fee and manual meal cost | Optional add-on prices, per person |
| Which cost line each amount came from | Properties considered but not offered, with a client-safe reason (§3.3a) |
| Why a property was *really* dropped, where the reason is commercial | *One total price, and nothing behind it* |

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
- **Confirmed against the real sheets (2026-08-24).** 24 of the 32 machine-readable
  documents state the tax basis explicitly and all of them are inclusive — Temple Point
  is typical: "Rates are per room per night, in KSH & inclusive of all taxes." The
  inclusive default is therefore the common case, not an assumption.
- **Implemented 2026-08-25 (Stage 3.7), having been documented and not built.** The rule
  above was stated from the start; the code stored whatever it was given and set
  `vat_inclusive` to match. Since nothing downstream adds tax, a sheet marked exclusive
  under-charged by the whole VAT rate while the document still told the client the price
  included it. Normalisation is now a single pure function, `app.core.vat.to_vat_inclusive`,
  applied **at write time** at both doors a rate can arrive by — a confirmed supplier
  document and a hand-entered rate — so:
  - `vat_inclusive` on a stored row is true by construction. It is **provenance**, not a
    flag any reader has to act on; the basis the source stated is what the input carried.
  - `vat_pct` is the rate the row was normalised at, so a VAT change or a zero-rated
    supplier needs no code change.
  - The function is idempotent, which is what stops a re-confirmed sheet being taxed twice.
  - Write time rather than read time on purpose: pricing reads rates from five places, and
    a gross-up applied at read time is a rule five call sites must remember whose failure
    mode is a silent under-charge. Applied on the way in, it is a property of the data.
  - Every money column on a rate shares one basis — the child rate and the single
    supplement are grossed up with the nightly rate, never left behind on the other one.

### 3.3 Rooming

- `rooms_required = ceil(pax ÷ room_capacity)`. Twin-sharing is the capacity-2 case
  (25 pax → 13 rooms); Pendo's 4-guest villas are the capacity-4 case (25 → 7).
- **An odd single room is charged in full**, not half. 25 pax = 12 twins + 1 room at
  the full room rate.
- The charge is **per room**, not per person.
- **Corrected 2026-08-24 — the price of that room depends on how many sleep in it.**
  The earlier rule here said a room costs the same whether 1 or 2 people occupy it.
  The supplier documents contradict that: 26 of the 32 readable sheets quote a separate
  figure per occupancy. Temple Point 2027/28, Creek Deluxe, full board, high season:

  | Occupancy | Rate per room per night (KSH) |
  |---|---|
  | Single | 28,400 |
  | Double | 37,600 |

  A single is therefore neither half a double nor equal to one — it is its own quoted
  price. So 25 pax in twins is 12 rooms at the double rate plus 1 at the single rate,
  and `occupancy` is part of rate identity, not a rooming detail (see §4).
- The "odd single charged in full" rule above still holds in the sense that the room is
  never half-charged; it is charged at the supplier's single-occupancy rate.
- **Corrected again 2026-08-25, while implementing 3.3.** This section previously said that
  where a sheet gives no single rate, `single_supplement` on top of the shared rate is the
  fallback. That is only coherent on a sheet priced **per person sharing**, which is how
  those 3 sheets are written. Our rates are stored **per room**, so adding a 4,000
  supplement to a 24,000 double would charge one guest 28,000 for the room two guests pay
  24,000 for. The engine therefore charges the next larger room **in full** — the other half
  of the same rule — and raises the stated supplement as a warning for review, because its
  presence is a hint the sheet may be per-person and was ingested on the wrong basis. Where
  no room the sheet prices can hold the guests, the room type drops out of the comparison
  rather than being mispriced.
- **Rate selection is per night, not per stay.** Season windows do not line up with
  itineraries: a 18-22 December booking crosses out of high season into festive, and pricing
  every night at the rate covering the arrival date would undercharge the last two by
  thousands per room per night. Where two rows overlap a night the later `effective_from`
  wins, the same tiebreak the Stage 2 lookup uses.

### 3.3a Minimum stay

Confirmed 2026-08-24. Nine of the thirty-two readable sheets state a minimum stay,
usually over the festive period — Temple Point: "Minimum stay between 20th Dec and 2nd
Jan : 4 Nights".

- **A property whose minimum stay the request does not meet is not offered.** The rate
  is not available for that stay, so pricing it anyway would quote a figure the supplier
  would refuse to honour.
- **It is still shown on the document as a missed-out option**, recorded in
  `quote_rejected_candidates` with the reason. This is the same mechanism the reference
  quotation uses for Diani Cottages (declined because it caps at 16 guests): the client
  sees that the property was considered and why it did not work, which reads as due
  diligence rather than an omission.
- The check is per stay, not per night: a 3-night request against a 4-night minimum
  fails even if only one of those nights falls inside the restricted window.
- `min_nights` already exists on `accommodation_rates`, so this is engine behaviour in
  3.3, not a schema change.
- **Rejection reasons are client-facing prose.** `reason` is rendered on the document
  verbatim, so it may only ever contain something safe to show — "requires a minimum
  stay of 4 nights over the festive period", never a cost, margin or supplier-relations
  reason. An internal-only rejection does not belong in this table (§2).
- **A missing rate is therefore not a rejection** (settled 2026-08-25 in 3.3). Only a rule
  the client can be shown becomes a `quote_rejected_candidates` row. A property with no rate
  loaded for those dates, or no room type that can house the group, is an **internal
  warning** instead: "we have no rates for this property" is a statement about our own data,
  not about the hotel, and printing it would invent a refusal the supplier never made.
- Where several room types each fail on a different minimum, the **shortest** minimum is the
  one quoted back — it is the easiest for the client to meet, so it is the honest one to
  state.

### 3.4 Meal plans

- Requested plan is picked per quote (full board or half board).
- **Fallback chain when a hotel has no rate for the requested plan:**
  `Full Board → Half Board → Bed & Breakfast + chef cost + manually entered meal cost`.
- A fallback is **flagged on the option** so the sales agent knows the plan differs
  from what was asked, and so non-comparable options can be marked (the sample already
  does this in prose for Pendo's "group meal arrangement").
- If the rate includes full board, meals are catered for — no separate meal line.
- **Chef cost applies only to BnB options and bed-and-breakfast hotel rates**, and is
  **entered manually per meal for the whole group** — one chef cooks for everyone, so it
  is a group fee, not a per-person one. Never added to a half-board or full-board option.
  The food cost itself is a separate manual entry, with the meal count derived from the
  stay length and the plan's gap (BB → lunch + dinner, so 2 per night; room-only → 3).
  Derived rather than typed, so a four-night bed-and-breakfast option cannot be quoted with
  three days of food. Half board leaves lunch but never takes a chef, so its gap is
  deliberately absent rather than set to 1.

### 3.4a Child pricing

- **Each hotel sets its own age policy** (a common one: a child over 11 is charged as an
  adult), so the bounds live with the property/rate, not globally — the same shape as
  the per-fee age bounds park fees already use.
- **Default is to charge a child as an adult**, and only depart from that where the
  hotel document says otherwise. Silence in a rate sheet means adult pricing, not a
  discount we invented.

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

- **STO rates win when present.** Sell-To-Operator / tour-operator rates are materially
  cheaper, so where a document carries one it is used as the accommodation cost directly
  — no comparison against a discounted rack figure, no rack manipulation, and margin on
  that line is the profit % alone.
- **Where there is no STO rate, use the rack rate as-is**, unless a discount has been
  supplied (either stated in the document or told to us), in which case the
  half-discount rule above applies.
- **The stated discount belongs to the document that stated it** (confirmed
  2026-08-24). A percentage is never global and never inferred from another sheet: the
  figure written on a given rate sheet is applied to that sheet's rates to arrive at
  our rate, which is why `supplier_discount_pct` sits on the rate row alongside
  `source_document_id`. Different properties state different numbers in different
  places — Baobab "10% Discount" and Reef "Issue 10% of this" in the document, Nyali
  "15% Commission to us" only in the filename — so the ingestion confirm step must ask
  for it per document rather than assume one house rule.
- The percentage is stored **as stated and never pre-applied**. Both derived figures
  (what we pay, what enters the build-up) are computed at pricing time, so any quoted
  price can be reconciled against the PDF it came from.

### 3.5a Supplements and compulsory gala dinners

Confirmed 2026-08-24 from the rate documents; previously unmodelled.

- 20 of the 32 readable sheets add a **festive supplement** on top of the nightly rate,
  and 8 make a **gala dinner compulsory**. Omitting them silently under-charges every
  December quote, so they are priced, not ignored.
- A supplement has its **own date window**, which is narrower than — and not aligned
  to — the season containing it. Temple Point: "Supplement Christmas: KSH 3300 per
  person per night (24.12 & 25.12)" and the same again for New Year, inside a festive
  season running 20.12–10.01.
- Each carries a **charging basis** (`per_person_per_night`, `per_person`,
  `per_room_per_night`, `per_room`) because the sheets differ, and the amount is
  meaningless without it.
- `is_mandatory` decides whether it is charged regardless of what the client asked for.
  Gala dinners normally are.
- Like every other stored amount it is VAT-inclusive by default, so it is never taxed
  a second time.
- Supplements are **internal cost lines** like any other: they raise the total the
  client pays but are not itemised to the client (§2).

### 3.6 Margin build-up (backend only)

Cost components, all normalised to VAT-inclusive:

1. Accommodation — cheapest eligible rate **within** each hotel (§3.7)
2. Meals + chef fee — only for BnB / bed-and-breakfast options (§3.4)
3. Park / conservation / entrance fees — **per person per day** of stay
4. **Mandatory** activities — listed with cost; appear in the document's *Included* list
5. Transport — transfers plus line-haul (§3.8)

then:

```
cost_subtotal   = sum(components)          # accommodation at STO, or the half-discounted rack
contingency     = cost_subtotal × 5%
cost_basis      = cost_subtotal + contingency
after_profit    = cost_basis × 1.24        # profit 24%, fixed
selling_total   = after_profit + agent_cover_fee    # manual, NOT marked up
per_person      = round_up_to_100(selling_total ÷ pax)
group_total     = per_person × pax         # per-person first, then multiply
```

- **Profit is a fixed 24%**, applied to the whole sum. It lives in the existing
  `app_settings["pricing"]` config (not hard-coded), with a per-quote override column
  for the exception case.
- **Contingency accrues profit** — it sits inside `cost_basis`, as modelled above.
- **Agent cover fee is manual and sits outside the profit calculation.** It is added
  after the 24%, so it is passed to the client at face value and never marked up.
  Entered per quote option and **never named on the document** — like profit and
  contingency, it is backend-only. The client sees one total price and nothing behind it.
- Realised margin on a discounted-rack option is therefore `24% + contingency + the
  retained half-discount`, which is why all three numbers are tracked separately.

**Per-person is computed first, then multiplied by headcount** to get the group total.
Doing it in that order (rather than rounding a group total and dividing) guarantees the
document's two headline numbers agree. The sample fails this — page 6 says KES
28,800/person while the page 11 table says 28,400, both against a 720,000 total.

Per-person is only meaningful when every traveller pays the same. It is **suppressed in
favour of the total booking price** when the group is not uniform:

- mixed traveller types (e.g. 1 adult + 1 child, §3.4a), or
- **mixed residency** (below).

### 3.5b Currency conversion

- **USD → KES is a fixed 130** (confirmed 2026-08-24). This is a *contract* rate, not a
  market rate: the supplier agreements state it themselves — Swahili Beach's 2026 STO
  contract reads "FOR RESIDENT RATES IN USD YOU MUST PLEASE USE CONVERSION RATE OF
  130 KES". Quoting at a market rate would disagree with the supplier's own invoice.
- It is stored as an ordinary effective-dated `exchange_rates` row seeded with
  `source='contract'`, **not** a constant in code, so an admin can supersede it when a
  supplier restates it and the change is auditable. Nothing business-related is
  hard-coded (see CLAUDE.md).
- Where two rates share an `effective_from`, the **later-entered** one wins. Without
  that tiebreak the effective rate was undefined and the same quote could price two
  ways on two runs — see the decision log.

### 3.6a Resident vs non-resident

The gap is large and it applies in **three** places, not one: **hotel rates, park and
conservation entrance fees, and activity fees**. All three already carry
`residence_category_id` in the existing schema, so no new structure is needed — the
residence category selected on the quote drives rate selection across every one of them,
and a rate missing for that category is a 404 rather than a silent fallback to the
resident price.

A group mixing residents and non-residents has no single per-person figure, so those
quotes show the total booking price only.

### 3.7 Option selection

- **Cheapest within a hotel** — across that hotel's room types for the eligible meal
  plan. The system never picks *between* hotels; the client is shown 3–9 hotels plus
  1–2 BnB options and chooses.
- Cheapest is decided on the **room rate for the whole stay**, before supplements. The
  sheets state supplements for the property rather than per room type, so bringing them into
  the comparison would not change the winner and would cost a supplement lookup per
  candidate room type.
- An option priced on a **fallback meal plan is marked not comparable** automatically. An
  agent may mark an option non-comparable for their own reasons (a villa against full-board
  resorts); the engine can only add that flag, never remove it.
- Options that fall back to a different meal plan, or whose structure isn't comparable
  (villa vs full-board resort), are flagged as such.
- **Rejected candidates are recorded and shown** with a reason — the sample's Diani
  Cottages entry (capacity 16 < 25 pax) demonstrates due diligence to the client. An
  agent-typed refusal and an engine-derived one are distinguished by
  `quote_rejected_candidates.source`, because re-pricing rewrites the engine's own refusals
  and a typed one is not rediscoverable from any rate.

### 3.7a What counts as a "BnB option"

Settled 2026-08-25 in 3.4. §1 asks for "3–9 hotels plus 1–2 BnB options", which needs a
definition of the split. The property `category` column is free text (`lodge`, `resort`,
`camp`, whatever an admin typed), so counting on it would be counting on a convention nobody
enforces.

The distinction is taken from **whether pricing had to add a chef**: an option resolved onto
a plan that leaves the guests to feed themselves is a self-catering option, and one resolved
onto half or full board is catered. That is derived from the rates rather than from a label,
and it is exactly the commercial difference the split is about. All four bounds live in
`app_settings["pricing"]` (`min/max_catered_options`, `min/max_self_catering_options`),
defaulted to the numbers above.

### 3.7b Readiness is graded

A quote can be **wrong** — an option that failed to price, a bed-and-breakfast option with
no chef cost, no recommendation to lead on, two recommendations, more options than the
template holds — or merely **thin**, offering two hotels where five would sell better. The
first kind blocks issuing; the second is advice returned alongside. One boolean would either
let an under-priced quote out or refuse a correct one.

`GET /quotes/{id}/readiness` returns both kinds and writes nothing. `POST /quotes/{id}/issue`
prices, then refuses on any blocking problem — reporting **all** of them at once, because
fixing them one 400 at a time is how the second one ends up in the client's copy.

### 3.8 Transport

Transport varies per quote and needs a segment model rather than the current
km-and-fuel vehicle costing.

**Access mode is a property of the destination.** Different places are reached
differently — some by rail, some by air, some only by road — so the available modes and
their tariffs are stored per destination and the agent picks one per quote. Which mode
is chosen then determines what else the quote must contain.

| Mode | Cost basis |
|---|---|
| Own/hired vehicle (Coaster, Land Cruiser) | per vehicle per day (existing model) |
| SGR | per person per one-way ticket, by class |
| Transfer | per leg, **priced by destination × vehicle type** |

**Air travel is never sold.** Heissal does not hold the licence to ticket flights, so
flight charges are excluded from every quote and "flight" is not an offerable line-haul
mode. Airport transfers remain quotable for a client who arranges their own flight — the
transfer is a road service, the ticket is not ours to sell.

**SGR tariffs** (per person, one way, seeded as the current values — held as
effective-dated rows in `destination_transport_modes`, never hard-coded, since fares
move):

| Class | Fare |
|---|---|
| Economy | KES 1,500 |
| Business | KES 12,000 |

A return journey is two segments, so a 25-pax economy round trip is
`25 × 1,500 × 2 = KES 75,000`.

- **Transfer prices key on both destination and vehicle type.** A Coaster transfer and a
  5–7 seater transfer are different prices for the same leg, and the same vehicle costs
  differently in different destinations. So transfers are a lookup table
  (destination, vehicle type/capacity class → price per leg), effective-dated like every
  other rate, not a figure derived from km and fuel.
- **Transfers are mandatory whenever line-haul is rail**: pickup → terminus,
  terminus → hotel, and the same in reverse. An SGR quote without transfer legs is
  incomplete and should be rejected by validation, not silently under-priced.
- **VVIP transport is an optional client-facing cost**, quoted as an add-on.

### 3.9 Optional extras

- Optional activities are priced **per person** and shown separately from the package
  (the sample's Wasini page carries no price — the generated document must).
- **Timed activities need a price ladder**, e.g. quad biking 10 min / 15 min / 30 min,
  each at its own price. Stored as tiers on the activity, rendered as a small table.
- **Alcoholic beverages stay "at an additional agreed fee"** — a POA line with no price.
- **Activities are location-based, exactly like hotels** — held against a destination and
  reused across quotes for that place, mandatory and optional alike. So choosing the
  destination is what populates the candidate activity list.
- Entrance fees charged inside an excursion price (the sample's Kisite-Mpunguti fees sit
  in the Wasini day trip) belong to the activity, not to the per-day park fee line —
  otherwise a 3-night stay would pay a one-day marine park fee three times.

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
- **Quotes are valid for 30 days** from issue, printed on the document. Past that the
  option must be re-priced rather than honoured, since supplier rates move — and because
  versions are immutable, re-pricing appends a new version and the expired one stays
  readable. `valid_until` is stamped when the quote is **issued**, not when it was drafted:
  a proposal built three weeks ago is still good for its full 30 days once it actually goes
  out.
- **Issuing is guarded by its own permission** (`quote:issue`), separate from
  `quote:create`. Assembling a quote and putting a price in front of a client are different
  levels of trust.
- **An issued quote refuses assembly edits.** Versions are immutable but the quote they hang
  off is not, and an option quietly added after the client received the document would make
  the stored version disagree with what they are looking at. Re-issuing is the supported
  path.
- The version's **headline figures come from the recommended option** — it is the one being
  proposed — with every option's figures kept beside it in `quote_version_options`, so
  "what did the client actually see" stays answerable. `internal_cost` on the version is
  what Heissal **pays**, not the costed subtotal: on a discounted rack rate those differ by
  the retained half, and calling the costed figure "cost" would understate realised margin by
  exactly that amount. Margin on the version is therefore profit + contingency + retained
  half, which is what §3.5 says it is.
- **The cover image is per destination**, not per quote: every Diani proposal opens on
  the same coastal cover, so the hero is an asset of the destination.
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

### 3.11a What 3.5 built, and the rules it settled

- **The document renders from the frozen version, never from live figures.** There is
  deliberately no way to render an unissued quote: the version *is* the document, and
  rendering live rates would produce a proposal whose numbers move between reloads.
  `GET /quotes/{id}/document.html?version=N` renders an earlier one exactly as the client
  received it.
- **Imagery is the one thing read live.** Photographs are presentation, not terms —
  replacing a dark photo of a hotel does not change what was quoted — and freezing image
  ids would leave an old document unable to show a re-cropped picture.
- **The view model is the boundary.** `QuotationView` has no field for cost, margin,
  supplier payments, contingency, profit or the agent cover fee, so no template edit can
  leak one. This is asserted against the *rendered bytes*, not against a schema.
- **Standing copy is configuration, not template literals.** The wordmark, contact
  details, "why us" list, availability notice, closing disclaimer, VAT note, tagline and
  page size live in `app_settings["document"]`. A hard-coded phone number on a
  client-facing document is a support ticket waiting to happen.
- **Type is reached through exactly two CSS custom properties.** A test asserts every
  `font-family` in the rendered page resolves through `var(--font-display)` or
  `var(--font-body)`, so the eventual swap to the real faces stays a two-line change
  rather than a hunt. Those two values are the only strings emitted into the stylesheet
  unescaped — HTML-escaping the quotes in a font name yields invalid CSS and silently
  drops the face — so they are charset-validated instead.
- **A section whose data is missing is omitted, not filled.** The transport page needs
  transport segments; the signature-experience page needs an activity flagged for its own
  section. A proposal describing transfers the client is not getting is worse than one
  that stays quiet about them.
- **Cropping is CSS, not a stored derivative.** A fixed aspect box with
  `object-fit: cover` *is* a centre crop and renders identically in print. Pre-cropped
  copies would need re-deriving on every layout change for a result the renderer gives
  free. A stored derivative earns its place only when an image needs a crop of its own.
- **The comparison table sorts cheapest first** while the option pages keep the agent's
  order — which is what the reference proposal does. The pages lead with the
  recommendation; the table lets a client scan on cost.
- **Paper is A4 by default.** The reference proposal was laid out on US Letter, but this
  document is printed in Kenya. It is config, since it is a property of the printer rather
  than of the design.

### 3.11b Printing to PDF (3.6)

- **Images are inlined as data URIs, in both the HTML and the PDF.** The 3.5 gap turned out
  to be wider than "a PDF problem": a browser opening the HTML document does not replay a
  bearer token when fetching an `<img>` either, so the linked version had broken images in
  every context that mattered. A self-contained document is the right artefact anyway —
  it can be saved and forwarded. `?inline_assets=false` keeps links for a preview whose
  fetcher can authenticate.
- **A `PdfRenderProvider` seam, with headless Chromium behind it.** The template was
  designed and visually verified in a browser, and CSS grid, `object-fit` and `@page` all
  behave there. A pure-Python engine needs no subprocess but does not implement grid, and
  would silently reflow every page. A hosted rendering API would plug in at the same seam.
- **A configured browser path is never second-guessed.** Two engines paginate differently;
  a client proposal must not change shape because a host had something else installed. An
  explicit path that is wrong produces an error, not a fallback.
- **Missing renderer ≠ broken renderer.** No browser on the host gives a message naming
  what to install and pointing at the HTML document, which still works. A browser that ran
  and failed reports the engine name and its output. Neither is a 500.
- **The PDF is not cached.** It would have to be keyed on the version *and* on the brand
  copy, the fonts and the paper size — all admin-editable — so a cache would keep serving
  the old phone number after someone corrected it. A second per render is cheaper than that
  class of bug. If PDFs later need attaching to email they can be stored then, fingerprinted
  against the config they were produced from.
- **The filename is `HTQ-YYYY-NNNN-vN.pdf`.** Two versions of one quote are two documents,
  and a support conversation about "the PDF you sent" has to be able to tell them apart.
- **Uploads are decoded before they are accepted.** The declared content type is a claim;
  decoding is the check. A corrupt file used to upload, store and embed without complaint
  and then render as alt text across the hero of a proposal — found exactly that way, by
  looking at a printed cover. The dimensions fall out of the same decode, which is why
  `width`/`height` stop being permanently NULL.

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
| `transport_segments` | The modes chosen on one quote: line-haul + transfer legs |
| `destination_transport_modes` | Which modes reach a destination (road / rail / air) and their per-person or per-vehicle tariff, effective-dated |
| `transfer_rates` | (destination, vehicle type/capacity) → price per leg, effective-dated |
| `destination_images` | Cover hero per destination (§3.11) |
| `accommodation_supplements` | Festive loadings and compulsory gala dinners: own date window, charging basis, mandatory flag (§3.5a) |

Column additions:

- `accommodation_rates`: `vat_inclusive`, `vat_pct`, `rate_kind` (`rack` / `sto`),
  `supplier_discount_pct` (as stated by the supplier), `source_document_id`,
  `child_min_age` / `child_max_age` / `child_rate` (per-property policy, §3.4a),
  **`occupancy`** (§3.3 — how many guests the price covers)
- The `accommodation_rates` uniqueness key is
  `(room_type, meal_plan, residence_category, occupancy, effective_from)`. `occupancy`
  is part of rate **identity**: without it the Single/Double/Triple rows of a real
  sheet collide and the sheet cannot be stored at all.
- `activities`: `is_mandatory`, `has_own_section` (already keyed to a destination)
- `quotes`: `pax_count`, `profit_pct`, `contingency_pct`, `requested_meal_plan`,
  `valid_until`
- `quote_options`: `agent_cover_fee`, `chef_fee_per_meal`, `manual_meal_cost`,
  `meal_plan_fallback_from`, `is_comparable`
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

### 5b. What the deterministic parser actually reads (measured 2026-08-24)

Measured by running the extractor over all 35 documents with the uploader declaring
currency and board basis, as the confirm flow does.

| Outcome | Documents | Confirmable rows |
|---|---|---|
| **Usable** — rows complete enough to confirm as they stand | **8** | 761 |
| **Partial** — prices, rooms and meal plans read; occupancy and/or dates not | 12 | 0 |
| Unrecognised — no rate grid found | 12 | 0 |
| Image-only scans — need a vision provider | 3 | 0 |

Two readers cover two layout families (§5c). The eight usable documents are both Swahili
Beach contracts, both Baobab sheets, both Temple Point sheets, Hemmingways and Reef.

**This is not "every hotel imported", and the gap is structural rather than a bug.**
What blocks the remaining sheets:

- **Occupancy** is missing on every row of all twelve partial documents, because those
  sheets key rates by room category and never state how many guests a price covers. This
  is recoverable in one action: `defaults` on the confirm request sets it for the whole
  batch (§5d).
- **Dates** are missing on most partial rows, and those rows carry no season name either,
  so they cannot be filled from a season definition or a shared default. Each of Pride
  Inn, Nyali, Medina Palms, CityBlue and Mukima Manor arranges its seasons differently,
  so this is per-sheet parser work — roughly a dozen distinct layouts.
- **Three documents contain no text at all.** Peak Hotels is two page images; both Soames
  sheets have neither text nor images, meaning the content is vector outlines. No parser
  can read these; they need OCR or a vision model, which is a decision and an API key
  rather than a code change.

The working answer for anything unreadable is unchanged and deliberate: the document is
stored, the sheet is reported as unrecognised rather than empty, and its rates are entered
by hand against it. Improving coverage never changes the ingestion contract.

Extraction cost, for reference: the largest real document (18 pages, 9 MB) takes about six
seconds per reader. If sheets get much larger, `/extract` should move to a background job.

### 5c. The two layout families

| Family | Shape | Reader | Examples |
|---|---|---|---|
| Row-per-season | Occupancy is a column, the season window is a row | `GridRateExtractor` | Swahili Beach, Baobab |
| Transposed block | Room name heads a block, meal plans are columns, occupancy is the row label | `BlockRateExtractor` | Temple Point |

Both run on every document and the one producing more confirmable rows wins; the summary
names it (`pdf-composite:pdf-block`). There is no reliable way to tell the families apart
before parsing, and results are never merged — see the decision log.

A third variant is read by the block reader: sheets keyed by **room category** rather than
occupancy (Turtle Bay). Those rows come out without an occupancy, which is correct, because
the sheet does not state one.

### 5d. Confirming a partly-read sheet

`POST /supplier-documents/{id}/confirm` accepts `defaults` alongside `rows`: values applied
to every row that does not state its own. This is what makes a partial document usable —
the residence category is never printed on any sheet in the corpus, and occupancy is absent
from twelve of them, so without it a reviewer would retype one value a hundred and fifty
times. A row's own value always wins, so a default can fill a blank but never overwrite what
the parser read.

### 5a. The real corpus (surveyed 2026-08-24)

`H:\Tours\Hotel Prices` — 35 PDFs covering roughly 24 properties, all 2026/27 or
2027/28. Nothing from it is committed to this repo: supplier contract rates are
confidential and belong in the database, not in git history.

| Property of the corpus | Finding |
|---|---|
| Machine-readable text layer | **32 of 35** |
| Image-only scans (need OCR/vision) | 3 — Peak Hotels rack, Soames NETT ×2 |
| Price by occupancy | 26 of 32 |
| Named seasons (HIGH/LOW/SHOULDER/PEAK/EASTER) | 21 of 32 |
| Explicit VAT basis, all inclusive | 24 of 32 |
| Festive supplement | 20 of 32 |
| Compulsory gala dinner | 8 of 32 |
| Child policy | 23 of 32 |
| Minimum-stay rule | 9 of 32 |
| Resident / non-resident split | 15 of 32, usually as **two separate documents** |
| Not rate sheets at all | 3 — one agent policy document, two activity brochures |

**Text-layer extraction must be grid-aware, not line-based.** Naive line extraction
(`pdftotext -layout`) silently misaligns: on the Swahili Beach contract the season
labels and the price rows came out offset by several lines, which would bind a rate to
the wrong date window — plausible output, wrong answer, no error raised. Reconstructing
rows from word *coordinates* fixes it (proved with `pdftotext -table` on the three
hardest layouts: Swahili Beach, Baobab's stacked season blocks, Temple Point's two
side-by-side seasons). Build against a declared Python library that exposes word
positions rather than shelling out to a binary that happens to be installed.

Consequences for the pipeline:

- The deterministic parser is the primary path and vision/OCR is the **fallback**,
  behind the same provider seam. Not the reverse — that ordering keeps per-document
  cost near zero for the documents it does handle. But see §5b: it reads far fewer of
  them today than "has a text layer" suggested.
- Residence category is a property of the **document**, so the confirm screen asks for
  it once per upload rather than per row.
- The discount percentage is often only in the **filename** ("15% Commission to us"),
  not the page text, so it must be a confirmed field, never a parsed one.
- `BO` / "bed only" appears in 4 sheets and maps to the existing `RO` (Room Only) meal
  plan — a mapping table, not a new plan.

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

## 8. Resolved

| Question | Answer |
|---|---|
| Image storage | Bytes on disk/object storage, metadata in Postgres (§6) |
| Fonts | Close equivalents as a declared placeholder until the real files arrive (§3.11) |
| Profit vs retained half-discount | Independent — profit applies to the whole sum; the retained half is margin on top (§3.5) |
| Profit percentage | **Fixed 24%**, in pricing config with a per-quote override (§3.6) |
| Contingency placement | Inside `cost_basis`, so profit accrues on it (§3.6) |
| Transfer costs | Priced by destination × vehicle type, effective-dated (§3.8) |
| Transport modes | A property of the destination — road/rail/air differ per place (§3.8) |
| Chef cost | BnB and bed-and-breakfast options only, entered **per meal** (§3.4) |
| Mixed bookings | Show the total booking price, no per-person figure (§3.6) |
| Child pricing | Per-hotel age policy; **default is adult pricing** unless the document says otherwise (§3.4a) |
| Rack vs STO | STO wins when present; otherwise rack as-is, or rack with a supplied discount halved (§3.5) |
| Entrance fees | Charged **per day** of stay; excursion entrance fees belong to the activity (§3.6, §3.9) |
| Rounding direction | Per-person first, then multiply by headcount (§3.6) |
| Quote validity | **30 days** (§3.11) |
| Activities | Location-based, like hotels (§3.9) |
| Cover image | Per destination (§3.11) |
| Agent cover fee | Manual, added **after** profit, never marked up, **never named on the document** (§3.6) |
| Chef fee basis | Whole group per meal, entered manually (§3.4) |
| SGR fares | Economy KES 1,500 / business KES 12,000 per person one way, effective-dated (§3.8) |
| Air travel | **Never sold** — no ticketing licence; flights excluded from every quote (§3.8) |
| Non-resident pricing | Applies to hotel rates, entrance fees AND activity fees; already modelled (§3.6a) |

## 8a. Resolved 2026-08-24 (this round)

| Question | Answer |
|---|---|
| USD to KES conversion | Fixed at **130**, seeded as contract data (§3.5b) |
| VAT basis on stored rates | **Inclusive**, confirmed across the corpus (§3.2) |
| Resident vs non-resident | Confirmed as a real split, and it arrives as separate documents (§3.6a, §5a) |
| A document's stated discount % | Applies to **that document's** rates to give our rate (§3.5) |
| Minimum stay not met | **Refuse the property, show it as a missed-out option** (§3.3a) |

## 9. Open questions

1. ~~**The real font files**~~ — **closed 2026-08-25.** Cormorant Garamond for display
   (cover headline, section headings, property names, price figures, italic taglines) and
   Libre Franklin for body and UI, with the client's full px type scale. Both are committed
   as variable woff2 under `app/modules/documents/fonts/` and embedded as data URIs; see
   §13.
2. **Airport transfers** — assumed still quotable for a client who books their own
   flight, since the transfer is a road service even though the ticket is not ours to
   sell (§3.8). Confirm, or exclude anything air-adjacent entirely.
3. **Occupancy beyond triple** — sheets stop at triple; a 4-guest villa is a different
   room type with its own rate. Assumed sufficient.
4. **Cancellation and payment terms** — stated on 21 sheets and currently not stored
   anywhere. They are contract terms rather than pricing inputs, so they are out of
   scope for pricing, but they may belong on the quotation document as disclosure.

## 10. Build order

- **3.1** Schema + migration for options, rejected candidates, images, price tiers,
  transport segments, rate provenance columns — *done*
- **3.1b** Occupancy as part of rate identity, `accommodation_supplements`, seeded
  contract FX rate — *done*. Forced by the real corpus: without it 26 of 32 sheets
  could not be stored and every December quote under-charged.
- **3.2** Rate ingestion: upload → extract → confirm → stored rates — *done*.
  Content-addressed storage, a provider seam, a deterministic grid parser, and a
  confirm step that is the only path able to create a rate. Parser coverage is
  partial (§5b) and improves without changing the contract.
- **3.3** Option pricing: cheapest-within-hotel, meal fallback chain, rooming rule,
  discount/STO handling, contingency + profit, per-person rounding
- **3.4** Multi-option quote assembly + recommendation + rejected candidates
- **3.5** HTML template matching the sample, section-by-section
- **3.6** PDF generation, quote number, validity
- **3.7** Correctness tests: the sample reproduced end to end, VAT normalisation,
  discount halving, rooming edge cases (odd pax, capacity-4), fallback chain

---

## 11. What 3.7 actually tests (2026-08-25)

The milestone list above names five things. How each is covered, and where:

| Rule | Where | Shape |
|---|---|---|
| VAT normalisation (§3.2) | `tests/test_vat.py`, `tests/test_stage3_correctness.py` | pure function incl. idempotence and a rate sweep; then both write doors, and a check that pricing adds no tax on top |
| Discount halving (§3.5) | `tests/test_option_rules.py` | swept over `None, 0, 7.5, 10, 15, 33.333, 100` — the three-number identity, `paid ≤ costed ≤ rack`, and STO exempt |
| Rooming (§3.3) | `tests/test_option_rules.py`, `tests/test_stage3_correctness.py` | every pax 1–40 × capacity 1–6: nobody lost or invented, no room overfilled, at most one short room; then 25-in-twins and 25-in-villas priced through the service |
| Fallback chain (§3.4) | both files | that half board is preferred to bed and breakfast, and that the middle step takes no chef |
| The sample end to end | `tests/test_stage3_correctness.py` | quote → price → issue → client read → internal read → rendered HTML, asserting the figures agree at every layer |

Two things are asserted there that are worth naming separately, because they are about the
document being safe rather than correct:

- **No internal figure reaches the page.** Checked against the rendered bytes with thousands
  separators stripped, so a figure cannot hide behind formatting.
- **No internal *word* reaches the page.** "Contingency" on a client document invites a
  question no agent wants to answer, even with no number beside it. Checked against the
  visible text with stylesheets and markup removed — the first version of that assertion
  failed on the `margin` in the page's own CSS, which is how a useful check gets deleted
  instead of fixed.

Deliberately **not** asserted: the reference PDF's own figures. Its page 6 and page 11
contradict each other (28,800 vs 28,400 per person against one 720,000 total, §3.6), so
reproducing it number-for-number would mean reproducing an arithmetic error. What is
reproduced is its **shape** — a 25-pax group, several options at different board bases, one
recommended, one property declined with a printed reason — against seeded rates whose
expected results are worked by hand.

---

## 12. Cohorts, currencies and cost bases (§3.6b — confirmed 2026-08-25)

Stage 3 priced a quote as one headcount at one residency in one currency, visiting one place.
The client confirmed that none of those hold in general. This section records the model that
replaces it; `app/modules/quotes/cohorts.py` is the implementation and its docstring is the
short version.

### 12.1 The group is cohorts

A **cohort** is a set of travellers who all pay the same price in the same currency:
`(residence, traveller_type)` with a count. Built from *counts*, not from named traveller
rows, because that is how a group booking is quoted — "twenty-five people, six non-resident,
two children". Named travellers stay available for passport-level detail at booking time;
they are not what pricing needs.

**Currency is a property of the residency**, not of the cohort: a resident adult and a
resident child are billed on the same sheet, so cohorts of one residency disagreeing on
currency is a data error and is refused.

| Cohort | Charged in | Why |
|---|---|---|
| Resident | KES | The hotel bills us in shillings for them |
| Non-resident | USD | The hotel bills us in dollars, so quoting in dollars passes the exchange risk to the party the currency belongs to instead of us absorbing a month of drift |

The group total is stated in one currency, converted at the **contract rate, disclosed on the
document**. A converted total with an unstated rate is a dispute waiting to happen.

### 12.2 Two partitions, and they are not the same

The single most expensive thing to get wrong here. See the decision log entry; in short:

| Job | Partitioned by | Because |
|---|---|---|
| Rooming | residency **only** | A room is priced per room at one residency; a mixed room has no defined rate |
| Charging | residency **and** traveller type | A child pays a child rate — but sleeps in their parents' room |

Per-residency rooming costs the occasional extra room (three residents plus three
non-residents is four twins, where six of one residency is three). That is the price of mixed
groups being quotable at all.

### 12.3 Every cost is (amount, currency, basis)

An amount is meaningless without its basis — the same 3,300 is a very different number per
person per night than per room, and the sheets use both. So the basis travels with the amount
and the multiplier is derived from the group, never hand-computed at the call site. This
generalises what `supplement_cost` already did for four bases.

| Basis | Multiplier |
|---|---|
| `per_person_per_night` | pax × nights |
| `per_person_per_day` | pax × days |
| `per_person` | pax |
| `per_room_per_night` | rooms × nights |
| `per_room` | rooms |
| `per_group_per_night` | nights |
| `per_group` | 1 |

**Nights and days are deliberately separate.** A stay is counted in nights; park and
conservation fees are charged per 24-hour period of presence, which for an overnight leg is
the same count but for a day excursion is one against zero nights. Conflating them either
loses a day of fees or invents a night of a hotel.

A line naming a **traveller type without a residence** is not expressible: "all children"
would have to be priced at two residencies' rates at once.

### 12.4 Attribution

- A line scoped to a residency **and** a traveller type goes to that cohort.
- A line scoped to a residency is shared **within** it, per head.
- A line scoped to neither is shared across the group, per head — a seat on a coach costs the
  same whoever is in it.
- A line for a cohort nobody is in (a child rate on an all-adult group) is **dropped**, never
  reassigned; charging somebody else's rate to whoever is left is the wrong answer.

### 12.5 The order of operations, and why

```
costs built on the whole group          # 13 rooms, one vehicle, fees summed
  -> attributed to cohorts             # per-head for shared, split before converting
    -> per cohort: + contingency, x profit, + agent-fee share
      -> per-person, rounded UP to the step
        -> cohort total = per-person x headcount
          -> group total = sum of cohort totals, converted
```

Every figure then reconciles with every other: residents' rate × residents, plus
non-residents' rate × non-residents, equals the group total exactly. Dividing a rounded group
total instead is what makes the reference proposal contradict itself (§3.6) — and the client
asked for group-level costing, which this gives, without inheriting that defect.

One consequence to hold on to: rounding up is applied per person and then multiplied out, so
the slack above cost is up to one rounding step **per traveller** — as much as 2,500 on a
25-person booking.

---

## 13. Typography (§3.11 — confirmed 2026-08-25)

| Role | Face | Where |
|---|---|---|
| Display | **Cormorant Garamond**, 400–700 + italic | cover headline, section headings, property names, price figures, at-a-glance values, italic taglines and VAT footnotes |
| Body / UI | **Libre Franklin**, 300–700 | paragraphs, spec-grid labels and values, tables, checklists, contact details, uppercase letterspaced eyebrows |

### 13.1 The files are in the repo

Three variable woff2 files in `app/modules/documents/fonts/`, embedded as data URIs by
`fonts.py`. Not linked from Google Fonts: the PDF renderer opens a local `file://` page, so
an unresolvable font request yields a proposal set in a fallback at different metrics with no
error raised — the same reasoning that made photographs self-contained in 3.6.

Three files rather than nine because both families are variable; the five Cormorant weights
the client listed download as five identical files. 112 KB, and every intermediate weight
works.

### 13.2 The scale

Specified in px on a 96dpi A4 artboard, so every template value is that figure **× 0.75**.
Body at 14px → 10.5pt is what identifies the artboard: it lands exactly on the print
convention the template already used.

Held as `--fs-*` custom properties at the top of the template. Two things to know before
editing them:

- `--fs-heading` (42px) is **larger** than `--fs-title` (40px). Not a mistake to tidy — they
  are different page roles in the reference: a section heading inside a page against the
  title of a comparison or closing page.
- Nothing in the template may name a font outside `--font-display` / `--font-body`, and a
  test enforces it. That rule is why swapping the placeholders for the real faces was a
  two-line edit to `DocumentConfig`.

### 13.3 What the scale cost, and where the space came from

The specified display sizes are much larger than the layout was built for, and applying them
pushed the running footer off three pages — each then taking a sheet carrying nothing but
that footer. The space was returned from **imagery** (option hero 52→44mm, gallery 26→22mm)
and from **leading that was too loose for display type** (the closing tagline was set at 1.7,
which is body leading). The client's type sizes were not altered.

Verified by rendering and measuring, not by reading CSS: rasterise the pages and find the
lowest inked row. Content was ending at 91% of a page whose safe area stops at 87%. The PDF
page count is asserted exactly rather than bounded, because a silent extra page *is* this
bug.
