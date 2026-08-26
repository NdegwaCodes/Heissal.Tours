# Hotel rate intake templates

Four CSV files. Open them in Excel or Google Sheets, fill them in, save as CSV, send them
back. They exist for the rate sheets the parser cannot read — of the 35 real supplier
documents, 8 are fully machine-readable, 12 partial, 12 unrecognised and 3 are text-free
scans (see the Stage 3 design doc §5b). This is the path for the other 27.

Fill them in this order, because each refers to the one before it by **name**:

| File | One row per | Rows for a typical property |
|---|---|---|
| `1-properties.csv` | property | 1 |
| `2-room-types.csv` | room type | 2–5 |
| `3-rates.csv` | price | **10–40** |
| `4-supplements.csv` | festive loading or compulsory extra | 0–4 |

`3-rates.csv` is where the work is, and where every expensive mistake lives. The rest of
this file is about that.

**Delete the `EXAMPLE` rows before sending.** They are there to show the shape of a real
sheet, not to be imported.

---

## The five things that go wrong

### 1. One row per occupancy, not one per room type

**26 of the 32 readable sheets quote a different price per occupancy.** A single is neither
half a double nor the same as one — it is its own published figure:

| Occupancy | Creek Deluxe, full board, high season |
|---|---|
| 1 | KES 28,400 |
| 2 | KES 37,600 |

So that room needs **two rows**. If you enter only the double, a lone guest cannot be
priced at all, and a group of 25 in twins — which is twelve doubles **plus one single** —
gets the wrong figure for that last room.

If the sheet genuinely quotes one price whatever the headcount, enter one row at the
occupancy it is sold for.

### 2. `rate_basis` — is the price for the room, or for one person?

Most sheets price **per room per night**. A few price **per person sharing**, and the two
look identical on paper. Getting it wrong halves or doubles the accommodation cost of every
quote using that property.

- `per_room` — the figure is what the room costs, whoever is in it. **Use this unless the
  sheet says otherwise.**
- `per_person_sharing` — the figure is what *one guest* pays. Enter the sheet's number
  as-published and put `per_person_sharing` here; the conversion is done on import, so
  there is no arithmetic for you to do and no chance of it being done twice.

### 3. `vat_basis` — say what the sheet says, not what you assume

Most Kenyan sheets are VAT-inclusive and that is the default. But **if the sheet says
exclusive, you must put `exclusive`**, because nothing downstream adds tax: the quotation
tells the client the price includes VAT, so an exclusive figure entered as inclusive
under-charges by the whole 16% *and* makes the document untrue.

Enter the figure exactly as printed either way. The gross-up happens on import.

### 4. `rate_kind` and `supplier_discount_pct` — never pre-apply a discount

| Sheet says | `rate_kind` | `supplier_discount_pct` | `rate_per_night` |
|---|---|---|---|
| "STO rates" / "our rates" | `sto` | blank | as printed |
| "Rack rates" with no concession | `rack` | blank | as printed |
| "Rack, less 15% for operators" | `rack` | `15` | **the rack figure, 24,000** |

Enter the rack figure and the 15 separately. Do **not** enter 20,400.

Two reasons. Any quoted price has to be reconcilable against the PDF it came from, and a
pre-netted rate cannot be. And a stated discount on a rack rate is **halved** to the
client — the client sees half the concession and Heissal retains the other half — which
only works if the system knows the original figure. Enter 20,400 and that margin is lost
silently.

### 5. Dates: `YYYY-MM-DD`, always

`2026-07-01`, never `01/07/2026`. Excel reads day-first or month-first depending on the
machine's locale, and `03/04/2026` is a valid date under both readings — so the error does
not announce itself, it just prices your April stay at March rates.

If Excel reformats a column, set that column's format to Text before typing.

---

## Column reference

### `1-properties.csv`

| Column | Required | Notes |
|---|---|---|
| `property_name` | ✔ | The exact spelling used in the other three files |
| `destination` | ✔ | Diani, Maasai Mara, Amboseli, Nairobi… Park fees attach to this |
| `category` | | `resort`, `lodge`, `camp`, `villa`, `guest_house`, `hotel` |
| `star_rating` | | 1–5, blank if unrated |
| `check_in_time` / `check_out_time` | | 24-hour, `1400` / `1000` |
| `child_min_age` / `child_max_age` | | The property's own child band, e.g. 3 and 11. **Leave blank if the sheet is silent — blank means a child is charged as an adult, which is the correct default, not a discount we invent.** |
| `supplier_name` | | The contracting entity, if it differs from the property |
| `contact_email` / `contact_phone` / `website` | | For re-confirming rates |

### `2-room-types.csv`

| Column | Required | Notes |
|---|---|---|
| `property_name` | ✔ | Must match `1-properties.csv` |
| `room_type_name` | ✔ | Exactly as the rate sheet names it |
| `room_code` | | The property's own code, if it has one |
| `max_occupancy` | ✔ | How many people the unit **sleeps**. Rooming is `ceil(guests ÷ this)`, so a 4-guest villa takes 7 units for 25 people while twins take 13. |

### `3-rates.csv`

| Column | Required | Notes |
|---|---|---|
| `property_name`, `room_type_name` | ✔ | Must match files 1 and 2 |
| `meal_plan` | ✔ | `RO` room only · `BB` bed & breakfast · `HB` half board · `FB` full board · `AI` all inclusive |
| `residence` | ✔ | `citizen` · `ea_resident` · `resident` (foreign national with a Kenyan permit) · `african_citizen` · `non_resident` |
| `occupancy` | ✔ | How many guests **this price covers**. See trap 1. |
| `season_name` | ✔ | The sheet's own wording — "High season", "Festive", "Green season" |
| `valid_from` / `valid_to` | ✔ | ISO dates. One row per season window; rates are chosen per night, so a stay crossing seasons prices each night correctly and needs no special handling from you. |
| `currency` | ✔ | `KES` or `USD` — as the sheet quotes it, not what we bill in |
| `rate_per_night` | ✔ | As printed. No discount applied, no tax added. |
| `rate_basis` | ✔ | `per_room` or `per_person_sharing`. See trap 2. |
| `rate_kind` | ✔ | `sto` or `rack`. See trap 4. |
| `supplier_discount_pct` | | The stated percentage, un-applied. See trap 4. |
| `vat_basis` | ✔ | `inclusive` or `exclusive`. See trap 3. |
| `vat_pct` | | Defaults to 16 |
| `child_rate` | | Per child per night, if the sheet gives one |
| `child_min_age` / `child_max_age` | | Only if this rate's band differs from the property's |
| `min_nights` | | Minimum stay, if stated. A request that does not meet it is **not offered** — the property appears on the quotation as considered, with the minimum quoted back as the reason. |
| `single_supplement` | | Only if the sheet states one **instead of** a single-occupancy price. It is recorded and flagged for review rather than added, because a per-room rate plus a per-person supplement usually means the sheet is priced per person sharing — see trap 2. |

### `4-supplements.csv`

For festive loadings and compulsory extras. **20 of 32 sheets carry one and 8 make a gala
dinner compulsory**, so this file is usually not empty — and leaving it empty silently
under-charges every December quote.

| Column | Required | Notes |
|---|---|---|
| `property_name` | ✔ | |
| `label` | ✔ | The sheet's wording, e.g. "Christmas Eve supplement" |
| `kind` | ✔ | `festive` or `gala` |
| `basis` | ✔ | `per_person_per_night` · `per_person` · `per_room_per_night` · `per_room`. **The amount is meaningless without this** — 3,300 per person per night and 3,300 per room are very different numbers. |
| `amount`, `currency` | ✔ | As printed |
| `valid_from` / `valid_to` | ✔ | The supplement's **own** window, which is usually narrower than the season containing it. Temple Point loads Christmas on 24–25 December inside a festive season running 20 Dec – 10 Jan. |
| `is_mandatory` | ✔ | `yes` or `no`. A gala dinner is normally `yes` — charged whether or not the client asked. |
| `room_type_name`, `meal_plan`, `residence` | | **Leave blank for "applies to everything"**, which is the usual case. Only fill them in if the sheet limits the supplement to one room type or plan. |

---

## What happens to it

The figures are stored with the source named against them, so any price on a quotation can
be traced back to the sheet it came from. VAT normalisation and the discount split happen
on import, so what you type stays exactly what the supplier published — which is what makes
the reconciliation possible.

If a row cannot be imported you get told which row and why, and nothing partial is stored.

**Other costs are not in these files.** Park and conservation fees come from the KWS
schedule and are already loaded. Transport, transfers and activity pricing have their own
intake and arrive with Stage 3.10.
