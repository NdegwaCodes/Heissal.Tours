# Hotel rate intake — one sheet

**`hotel-rates.csv`** — open it in Excel or Google Sheets and fill it in. Save as
**.xlsx or CSV UTF-8**; both import.

One row per price. Property and room details repeat down the rows, which is deliberate:
copying a property name into five rows is less work and far less error-prone than keeping
four files in step by hand.

Delete the `EXAMPLE` rows before sending.

---

## If a sheet publishes both a rack rate and an agent NETT

Put **one row**, not two. Enter the rack figure in `amount` and the concession in
`discount_percent`:

| Sheet says | `rack_or_sto` | `discount_percent` | `amount` |
|---|---|---|---|
| "Rack 450. Agent NETT 360 (rack less 20%)" | `rack` | `20` | `450` |

Two rows for the same room-night also work — the importer recognises a rack/NETT pair and
folds them into one rate, deriving the percentage as `1 - nett/rack`. But one row is less
typing and unambiguous.

What does **not** work is entering only the NETT figure and calling it `sto` with no
discount. That tells the system we pay 360 and the client should be costed 360, so the
concession is handed to the client in full instead of split. Half of it is Heissal's margin.

Three or more rates for the same room-night can't be resolved and are left out with a report,
so don't use extra rows to record anything other than a rack/NETT pair.

## `row_type` — the first column, and the only one that changes what the others mean

| Value | Use it for | Charged |
|---|---|---|
| `RATE` | a room price | per the room, per night |
| `SUPPLEMENT` | a compulsory addition — festive loading, gala dinner | always, whether asked for or not |
| `EXTRA` | an optional add-on the client may decline | only if chosen |

`SUPPLEMENT` and `EXTRA` rows leave the room and occupancy columns **blank**, which means
"applies to the whole property". Fill them in only if the sheet limits the charge to one
room type or meal plan.

**20 of 32 real sheets carry a festive supplement and 8 make a gala dinner compulsory**, so
if a property has none, that is worth double-checking rather than assuming. An empty
supplement row silently under-charges every December quote.

---

## The five things that cost money

### 1. One row per occupancy, not one per room type

**26 of 32 real sheets quote a different price per occupancy.** A single is neither half a
double nor the same as one — it is its own published figure:

| `price_covers` | Creek Deluxe, full board, high season |
|---|---|
| 1 | KES 28,400 |
| 2 | KES 37,600 |

So that room gets **two rows**. Enter only the double and a lone guest cannot be priced at
all — and 25 people in twins is twelve doubles **plus one single**, so the last room takes
the wrong figure.

### 2. `charged_per` — for the room, or for one person?

Most sheets price per room. A few price *per person sharing*, and the two look identical on
paper. Getting it wrong is a factor of two on every quote using that property.

| Value | Meaning |
|---|---|
| `room_per_night` | what the room costs, whoever is in it — **the usual case** |
| `person_per_night` | what one guest pays per night. The sheet will say "per person sharing" |
| `person_per_stay` | once per person for the whole stay — a gala dinner, an excursion |
| `room_per_stay` | once per room for the whole stay |

Enter the sheet's number exactly as printed either way. The conversion happens on import, so
there is no arithmetic for you to do and no risk of it being done twice.

### 3. `vat` — write what the sheet says, not what you assume

Most Kenyan sheets are VAT-inclusive and that is the default. But **if the sheet says
exclusive, you must write `exclusive`** — nothing downstream adds tax. The quotation tells
the client the price includes VAT, so an exclusive figure entered as inclusive under-charges
by the whole 16% *and* makes the document untrue.

Enter the figure as printed either way. The gross-up happens on import.

### 4. Never subtract a discount yourself

| Sheet says | `rack_or_sto` | `discount_percent` | `amount` |
|---|---|---|---|
| "STO rates" / "our rates" | `sto` | blank | as printed |
| "Rack rates", no concession | `rack` | blank | as printed |
| "Rack, less 15% for operators" | `rack` | `15` | **24000** — the rack figure |

Enter `24000` and `15`. **Not** `20400`.

Two reasons. Any quoted price has to be reconcilable against the PDF it came from, and a
pre-netted rate cannot be. And a stated discount on a rack rate is **halved** to the client
— they see half the concession, Heissal keeps the other half — which only works if the
system knows the original figure. Enter 20,400 and that margin is gone without a trace.

### 5. Dates: be consistent, and `YYYY-MM-DD` is safest

`2026-07-01` leaves nothing to interpret. `01/07/2026` does: Excel reads day-first or
month-first depending on the machine's locale, and `03/04/2026` is valid under both — so the
error does not announce itself, it just prices your April stay at March rates.

Day-first `DD/MM/YYYY` is accepted, because that is what the sheets actually arrive in. The
importer works out which order a file uses by looking for dates whose first number is above
12, and reports what it decided. Two rules follow from that:

- **Never mix the two orders in one sheet.** If a file contains both, every date in it is
  suspect and nothing is imported.
- If no date in the sheet has a number above 12, the order genuinely cannot be told from the
  data. It is read as day-first and flagged — check a few before relying on it.

If Excel keeps reformatting a date column, set that column's format to **Text** before
typing.

---

## Every column

| Column | Rows | Notes |
|---|---|---|
| `row_type` | all | `RATE` · `SUPPLEMENT` · `EXTRA`. See above. |
| `property_name` | all | Repeat it on every row for that property. Spelling must be consistent — it is what groups the rows. |
| `destination` | all | Diani, Watamu, Maasai Mara, Amboseli… Park fees attach to this. |
| `room_type` | RATE | As the sheet names it. Blank on SUPPLEMENT/EXTRA = whole property. |
| `room_sleeps` | RATE | How many the unit **sleeps** — its capacity. Rooming is `ceil(guests ÷ this)`, so a 4-guest villa takes 7 units for 25 people where twins take 13. |
| `meal_plan` | RATE | `RO` room only · `BB` bed & breakfast · `HB` half board · `FB` full board · `AI` all inclusive |
| `guest_residence` | RATE | `citizen` · `ea_resident` · `resident` (foreign national holding a Kenyan permit) · `african_citizen` · `non_resident`. Blank on SUPPLEMENT/EXTRA = everyone. |
| `price_covers` | RATE | How many guests **this price is for**. Not the same as `room_sleeps` — see trap 1. |
| `label` | all | For a RATE, the sheet's season wording ("High season", "Festive"). For a SUPPLEMENT or EXTRA, its name ("Christmas Eve supplement"). |
| `valid_from` / `valid_to` | all | ISO dates. A supplement's window is usually **narrower** than the season around it: Temple Point loads Christmas on 24–25 Dec inside a festive season running 20 Dec – 10 Jan. |
| `currency` | all | `KES` or `USD` — as the sheet quotes it, not what we bill the client in. |
| `amount` | all | As printed. No discount applied, no tax added. |
| `charged_per` | all | See trap 2. |
| `rack_or_sto` | RATE | See trap 4. |
| `discount_percent` | RATE | The stated percentage, un-applied. See trap 4. |
| `vat` | all | `inclusive` or `exclusive`. See trap 3. |
| `child_amount` | RATE | Per child per night, if the sheet gives one. |
| `child_ages` | RATE | The band as one cell, e.g. `3-11`. **Leave blank if the sheet is silent** — blank means a child is charged as an adult, which is the correct default. We do not invent a discount the hotel never offered. |
| `min_nights` | RATE | Minimum stay, if stated. A request that does not meet it is **not offered** — the property appears on the quotation as considered, with the minimum quoted back as the reason. |
| `notes` | all | Anything the columns cannot hold. Read by a human, not imported. |

Rates are chosen **night by night**, so a stay crossing two seasons prices each night
correctly with no special handling from you. Just give each season its own row.

---

## Before you send

- [ ] Every `EXAMPLE` row deleted
- [ ] `property_name` spelled identically on all of that property's rows
- [ ] A row for **each** occupancy the sheet prices, not one per room type
- [ ] Discounts entered as a percentage beside the original rate, not subtracted from it
- [ ] `vat` matches what the sheet actually states
- [ ] Every date reads `YYYY-MM-DD`
- [ ] Supplements entered, or genuinely confirmed to be none
- [ ] The original PDF or photograph sent alongside

---

## What the importer will and will not fill in for you

**Forgiving about spelling.** `B&B` and `BB`, `BO` and `RO`, `STO` and `sto`, `Non-Resident`
and `non_resident` — all the same thing. None of those change a number, so none of them are
worth rejecting a sheet over.

**Strict about missing figures.** A row with no validity window, no `price_covers`, no
`room_type` or an unrecognised `meal_plan` is **left out and reported**. A guessed season
window is a price the supplier never quoted, and it would go on to price real quotes.

**`room_sleeps` can be left blank.** Capacity is taken as the largest `price_covers` on that
room type, and every inference is listed in the report so you can correct it. Fill it in where
a room sleeps more people than any rate prices for.

**Everything is re-importable.** Fix the sheet, import again: rates that already exist are
updated in place rather than duplicated.

## What happens to it

Figures are stored with their source named against them, so any price on a quotation can be
traced back to the sheet it came from. VAT normalisation, the discount split and the
per-person conversion all happen on import — what you type stays exactly what the supplier
published, which is what makes that reconciliation possible.

If a row cannot be imported you are told which row and why, and nothing partial is stored.

Park and conservation fees come from the KWS schedule and are already loaded. Transport,
transfers and activity pricing have their own intake.
