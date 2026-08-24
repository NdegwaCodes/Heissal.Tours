# Decision log

Why, not what — git history already records *what* changed. One entry per decision that
would otherwise be re-litigated. Newest last.

---

**Fresh backend rather than evolving the marketplace app** (2026-08-13)
The original tours-marketplace backend modelled a listings marketplace, not a quotation and
pricing engine; retrofitting cost more than restarting. Archived read-only under `legacy/`
so past logic stays greppable. `backend/` and `migrations/` at the repo root are leftovers
from that era and are not built.

**Build order: quote engine first, public website last**
The commercial core is quotation + pricing + itinerary; the website only sells what the
engine can price. Client-approved sequence — see `docs/roadmap.md`. Resisting "just ship a
landing page first" is the point of writing this down.

**One backend, many clients**
Admin, website, portal, and a future mobile app all consume the same FastAPI service. Keeps
pricing logic un-duplicated; the cost is that every feature needs role-aware serialization
from day one.

**Money as `NUMERIC`/`Decimal` + currency code, never floats**
Rounding drift in quotes is a commercial defect, not a cosmetic one. Currency travels with
the amount because FX and multi-currency supplier costs are in scope from Stage 2.

**Cost/margin split at the schema level, not the serializer**
Client-facing roles get a different schema, gated by `quote:read_cost` — not a filtered
version of the internal one. A forgotten field in a filter leaks margin to clients; a
separate schema fails closed.

**Admin talks to the API only through a server-side BFF (`/api/proxy/*`)**
JWTs stay in httpOnly cookies and never reach browser JS. Cost: an extra hop and a proxy
route per resource.

**Pricing config in `app_settings` JSONB instead of a new table** (Stage 2.6)
Markup/discount/tax config is read as a unit and edited rarely; a table plus migrations per
knob bought nothing. Revisit if it ever needs per-row history or querying.

**Quote versions are immutable and append-only** (Stage 2.8)
Pricing a quote appends a `QuoteVersion` + items and repoints `current_version_id`; nothing
is mutated. A sent quote must remain reproducible after rates change. `calculate` is the
non-persisting path.

**Catalogue modules are scaffolded from a JSON spec** (`scripts/scaffold_module.py`)
Seven near-identical CRUD modules had already been hand-written; the eighth made the pattern
worth generating. The script emits boilerplate only — rate selection, effective-dated
sub-tables, and pricing math stay hand-written, and it prints the manual wiring steps rather
than guessing them.

**Context repo kept to the minimal kit** (2026-08-24)
`CLAUDE.md` + `rules/` + memory + a decision log, with `.claude/agents/` empty. Contracts, a
plan/verify pipeline, and generated digests are deferred until their specific pain shows up —
see `docs/context-repo-quickstart.md`.

**Rates are stored VAT-inclusive; the engine adds no tax on top** (Stage 3, 2026-08-24)
Kenyan supplier documents quote inclusive of 16% VAT by default, so an exclusive-then-add
model would double-tax every rate that arrived inclusive. Exclusive sources are normalised
at ingestion (`x 1.16`) and each rate row records its VAT basis, so a rate is auditable
back to its document. VAT becomes a disclosure line, not an arithmetic step.

**Half of a supplier's stated discount is passed to the client, and profit stacks on top**
(Stage 3, confirmed 2026-08-24)
A document offering 15% off rack yields 92.5% of rack as the *costed* accommodation figure,
while Heissal pays 85%. The retained 7.5% is **not** counted inside the profit percentage:
profit is calculated on the whole sum, so realised margin on such an option is 24% plus
contingency plus the retained half. Three numbers per rate therefore have to be tracked
separately — what we pay, what we cost it at, and what we retain — or the margin reporting
silently understates itself. Where only STO/tour-operator rates exist there is no retained
half, and the profit percentage is the whole margin.

**Per-person is rounded up, and the group total is derived from it** (Stage 3)
Rounding per-person to the nearest 100 and multiplying back by headcount guarantees the
document's two headline numbers agree. The client's own sample quotation shows why: page 6
states 28,800 per person while the comparison table says 28,400, both against the same
720,000 total. Deriving one from the other makes that class of error impossible.

**A room is charged per room, and an odd single room is charged in full** (Stage 3)
Rooms required is `ceil(pax / room_capacity)` — twin-sharing is just the capacity-2 case,
villas the capacity-4 case. A 25-person group pays for 13 rooms, not 12.5: suppliers do not
half-bill an under-occupied room, so neither do we.

**Extracted supplier rates require human confirmation before they are stored** (Stage 3)
Hotel rate sheets arrive as PDFs and designed images, so extraction means OCR-grade
uncertainty on money values. A wrong parsed rate that reaches a client is a commercial
incident, not a bug, so extraction proposes and a person approves. The source file stays
attached to every rate.

**Destinations double as the geographic grouping for properties** (Stage 3)
"Diani" is a destination, and every accommodation in the sample quotation hangs off it;
parks and reserves are destinations of a different type. Adding a separate area/zone table
would buy nothing that `destination_id` plus the existing `region`/`country` columns do not,
and would turn "properties in this area" from an indexed FK lookup into a join.

**Profit is a fixed 24%, held in config** (Stage 3, 2026-08-24)
Chosen over a per-quote judgement call within a 20–25% band so that quotes are comparable
and reproducible, and so margin analysis has a constant to measure against. It lives in
`app_settings["pricing"]` with a per-quote override for the exception, never hard-coded —
same rule as every other business number in this system. Contingency (5%) sits inside the
cost basis, so profit accrues on it too.

**Image bytes live outside Postgres; only metadata is a row** (Stage 3, 2026-08-24)
5–6 photos per property need no transactional guarantees, and large `bytea` columns inflate
every backup and slow the catalogue queries that touch the table. Files sit on disk/object
storage behind the API so access stays permission-checked rather than resting on a
guessable public path. Originals are retained and the template's aspect ratios come from
centre-cropped derivatives, so a layout change can re-derive them without re-collecting
photographs.

**Template fonts are a declared placeholder behind two CSS variables** (Stage 3, 2026-08-24)
The brand faces aren't available yet and cannot be identified reliably from a PDF render,
so close equivalents stand in — but only via `--font-display` / `--font-body` defined in one
place, making the eventual swap a two-line change. The placeholder is labelled in the
template header so a stand-in is never mistaken for the real brand type.

**Agent cover fee is added after profit and never marked up** (Stage 3, 2026-08-24)
A manually entered amount that reaches the client at face value: `(cost + contingency) x
1.24 + agent_cover_fee`. Kept outside the profit calculation deliberately — marking it up
would inflate a pass-through, and burying it inside the cost basis would make it accrue
both contingency and profit. It is folded into the quoted total rather than itemised.

**STO rates are preferred outright, not compared** (Stage 3, 2026-08-24)
Sell-To-Operator rates are materially cheaper than any halved rack discount, so where a
document carries one it wins with no comparison step. Rack is the fallback, used as-is
unless a discount has been supplied, in which case half of it passes to the client. The
earlier "take whichever is cheaper" idea was dropped as a comparison that never changes
the answer.

**A child is charged as an adult unless the hotel says otherwise** (Stage 3, 2026-08-24)
Hotels set their own age policies (commonly: over 11 pays adult), so the bounds live with
the property's rate rather than globally, mirroring the per-fee age bounds park fees already
use. Where a rate sheet is silent, adult pricing applies — the system does not invent a
child discount, for the same reason it never guesses a missing rate.

**Transport mode is a property of the destination; transfers price on destination x vehicle**
(Stage 3, 2026-08-24)
Some places are reached by rail, some by air, some only by road, so the available modes and
their tariffs hang off the destination and the agent picks one per quote. Transfer prices key
on both destination and vehicle type — a Coaster and a 5-7 seater are different prices for
the same leg — which makes transfers a lookup table rather than something derived from km
and fuel like the safari vehicle model.

**Occupancy is part of rate identity** (Stage 3.1b, 2026-08-24)
Twenty-six of the thirty-two machine-readable supplier sheets quote a different price for
the same room, meal plan and season depending on how many people sleep in it — Temple
Point 2027/28 Creek Deluxe full board is 28,400 single against 37,600 double. The old
uniqueness key `(room_type, meal_plan, residence_category, effective_from)` had no room
for that, so those rows collided and most real sheets were literally unstorable. Adding
`occupancy` to the key rather than inventing a derivation (single = half a double, or
single = double) is the only faithful option: the sheets show a single is neither. This
also corrects the earlier §3.3 rule that a room costs the same however many occupy it,
which was inferred from a single sample quotation and contradicted by the corpus.

**Supplements are their own table, not columns on a rate** (Stage 3.1b, 2026-08-24)
A festive supplement has a date window narrower than and unaligned to the season that
contains it (Temple Point loads 24–25 December inside a festive season running 20.12–10.01),
its own charging basis, and applies across several room types at once. Folding it into the
rate row would mean duplicating it per room type and losing its window. Twenty sheets carry
one and eight make a gala dinner compulsory, so leaving it unmodelled meant every December
quote silently under-charged.

**The USD→KES 130 rate is seeded contract data, not a constant** (Stage 3.1b, 2026-08-24)
The rate is fixed at 130 by the supplier agreements themselves ("FOR RESIDENT RATES IN USD
YOU MUST PLEASE USE CONVERSION RATE OF 130 KES" — Swahili Beach 2026 STO contract), so
quoting at a market rate would disagree with the supplier's own invoice. It is still stored
as an ordinary effective-dated `exchange_rates` row with `source='contract'` rather than a
literal in code, so an admin can supersede it without a deploy and the change is auditable.
Until now the rate existed only inside individual tests, which meant a real deployment would
have raised NotFoundError on the first USD property quoted in KES.

**FX resolution breaks ties by entry order** (Stage 3.1b, 2026-08-24)
Two `exchange_rates` rows can share an `effective_from` — an admin correcting a rate
re-enters it for the same day — and the provider ordered by `effective_from DESC LIMIT 1`
alone, so the winner was whatever order Postgres happened to return. The same quote could
price two ways on two runs. Ordering by `(effective_from DESC, created_at DESC)` makes the
latest entry for a day authoritative, which is also the intended business meaning of a
correction. The bug was invisible because the test runner recreates the database each time;
it surfaced only when the suite was run twice against a dirty one.

**Text-layer PDFs are parsed by grid, not by line** (Stage 3.2 groundwork, 2026-08-24)
Line-based extraction of the supplier sheets misaligns silently: on the Swahili Beach
contract the season labels and price rows came out several lines apart, which would attach a
rate to the wrong date window and raise no error. Coordinate-based row reconstruction fixes
it, verified on the three hardest layouts. Because 32 of 35 documents carry a usable text
layer, the deterministic parser is the primary path and vision/OCR is the fallback for the
three image-only scans — the reverse ordering would put a per-document model cost on every
upload for no accuracy gain.

**A minimum stay not met removes the option but not the mention** (Stage 3, 2026-08-24)
Nine supplier sheets state a minimum stay, mostly over the festive period. Pricing a
property whose minimum the request misses would quote a rate the supplier would refuse to
honour, so it is not offered. Dropping it silently is the wrong other extreme: the client
cannot tell whether a well-known property was overlooked or ruled out. It is therefore
recorded in `quote_rejected_candidates` and printed on the document with its reason, the
same mechanism the reference quotation uses for Diani Cottages and its 16-guest cap. That
makes rejection reasons client-facing text by definition, so a commercial reason (margin,
supplier relations) must never be written there.
