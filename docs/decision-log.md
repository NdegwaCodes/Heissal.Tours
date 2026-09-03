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

**Extraction proposes; only a person creates a rate** (Stage 3.2, 2026-08-24)
Parsed rows land in `supplier_document_extractions` as proposals and only the confirm
endpoint writes to `accommodation_rates`. The reviewer can override every field, because a
confirm screen that cannot correct a misread number teaches people to click through it,
which removes the only safeguard the design has. The same suspicion applies to a vision
model later: the seam makes no distinction between a parser and a model.

**Uploads are content-addressed** (Stage 3.2, 2026-08-24)
Files are stored under the SHA-256 of their bytes, which gives deduplication, integrity
and path safety in one move: the stored name never derives from a user-supplied filename,
so no upload can traverse out of the root. Re-uploading the same sheet for the same
property is refused with a pointer to the existing review queue rather than creating a
second queue for identical rates — the corpus already contains a file named "... - Copy.pdf".

**pdfplumber rather than a pdftotext binary** (Stage 3.2, 2026-08-24)
The approach was proven with Xpdf's `pdftotext -table`, which happened to be installed on
one developer machine and would not exist on a deployment target. pdfplumber is a declared
dependency and exposes word coordinates, so row reconstruction is ours to control rather
than a side effect of someone else's layout heuristic.

**A sheet the parser cannot read is reported, never treated as empty** (Stage 3.2, 2026-08-24)
"No rates found" and "this document defeated the parser" are different facts, and conflating
them silently loses a property's entire price list. An image-only scan sets
`needs_other_provider`, an unrecognised layout says so, and the document is marked failed
with the reason attached. Coverage today is 5 of 35 documents fully readable (design doc
§5b), so this distinction is load-bearing rather than defensive.

**Money and dates are refused rather than guessed** (Stage 3.2, 2026-08-24)
Three concrete traps found in the real sheets drove this. Swahili Beach writes thousands
with a dot ("23.920 KES"), so reading it as a decimal understates a rate a thousandfold
while still looking plausible. Baobab puts prices immediately after a season label, and a
permissive year pattern read the price 280 as the year 0280. A merged cell holding
"280 370 500" would attach the single-occupancy price to every occupancy column. In each
case the parser now returns nothing and the row goes to a human.

**Two readers, chosen by result rather than by sniffing the layout** (Stage 3.2, 2026-08-24)
Supplier sheets use at least two incompatible layouts: occupancy as a column with the season
as a row (Swahili Beach, Baobab), and the transposed shape where a room name heads a block,
meal plans are the columns and occupancy is the row label (Temple Point). There is no
reliable signal for which a document uses, so both readers run and the one producing more
*confirmable* rows wins, with the winner named in the summary. Results are never merged: two
readers describing the same page produce every rate twice, and a reviewer cannot tell a real
duplicate from a parsing artefact.

**The transposed reader works from word coordinates, not table cells** (Stage 3.2, 2026-08-24)
On the Temple Point sheet the ruled table holds only the price rows — the room name, the meal
plans, the seasons and the date windows all sit outside it in page text — so cell position
alone cannot say what a price means. Each price is matched to the meal-plan heading above it
and the season block above that. Asking pdfplumber for a text-positioned table on those pages
was not an option either: it splits "26,500" into the cells "26", ",5" and "00".

**The document year is the commonest year named, not the earliest** (Stage 3.2, 2026-08-24)
Season windows are often written without a year because the sheet's title carries it. Taking
the earliest year in the document looked reasonable and was wrong on real files: the Medina
Palms 2026 contract carries a "MAY 2025" revision stamp, and the Swahili Beach 2026 contract
names 2025 twice against 2026 a hundred and seventeen times. Both would have had every
undated season shifted a year early, with dates that still looked entirely plausible. Ties go
to the earlier year, since a contract season spanning two years starts in the earlier one.

**A price must sit under a column heading to count as a rate** (Stage 3.2, 2026-08-24)
Allowing a room name as a row label — needed for the sheets that key rates by room rather
than occupancy — let prose become data: the Temple Point child policy paragraph produced rate
rows of 3, 12 and 60, which are ages. A line now needs at least two amounts aligned with
meal-plan columns before it is read as rates. Alignment is the real relationship being
modelled, so it is a better guard than any keyword blacklist.

**Confirmation accepts shared defaults** (Stage 3.2, 2026-08-24)
Most half-read sheets are missing the *same* field on every row, because the document never
states it — the residence category is never printed, and many sheets never name an occupancy.
Without shared defaults a reviewer would retype one value a hundred and fifty times, and a
confirm screen that tedious stops being read, which defeats the only safeguard in the
pipeline. A row's own value always wins, so a default can fill a blank but never overwrite
what the parser read or the reviewer chose.

**Per-person sheets are flagged, not converted** (Stage 3.2, 2026-08-24)
Some sheets price per guest ("Rates are per person sharing" — Turtle Bay) rather than per
room, which is what every stored rate means. Converting one to the other requires knowing the
occupancy the supplier assumed, which the sheets do not state, so those rows carry an explicit
warning and wait for a person instead. Deciding whether to model a per-person basis properly
is deferred until Stage 3.3 shows what the pricing engine needs. "per room" wins when a page
says both, because a sheet stating a per-room basis usually mentions per person only for a
supplement.

**A single supplement is never added to a per-room rate** (Stage 3.3, 2026-08-25)
§3.3 gave "single supplement on top of the shared rate" as the fallback where a sheet quotes
no single-occupancy price. Implementing it against the demo catalogue produced an absurdity:
a 4,000 supplement on a 24,000 double charges one guest 28,000 for the room two guests pay
24,000 for. The rule is only coherent on a sheet priced per person sharing, which is how the
three sheets that state a supplement are written — and our rates are stored per room. So the
next larger room is charged **in full**, which is the other half of the same section's rule,
and the stated supplement is raised as a warning, since its presence hints the sheet may have
been ingested on the wrong basis. The design doc has been corrected rather than the code bent
to match it.

**Rates are looked up per night, not once per stay** (Stage 3.3, 2026-08-25)
Season windows do not align with itineraries. A stay of 18-22 December crosses out of high
season into festive, and selecting one rate for the whole stay — the Stage 2 engine's shape —
would price those festive nights at the cheaper season, an undercharge of thousands per room
per night that no assertion on a total would reveal. The cost is one indexed query per
property and a loop in Python, not a query per night.

**A gap in our rate data is not a client-facing refusal** (Stage 3.3, 2026-08-25)
`quote_rejected_candidates.reason` prints on the quotation verbatim, so the table can only
hold reasons a client may see. A minimum stay the itinerary does not meet is one. "No rate is
loaded for these dates" is not: it describes our own catalogue, not the hotel, and rendering
it would invent a refusal the supplier never made. Those cases return an internal warning on
the pricing result, visible only to a role holding `quote:read_cost`.

**The internal/client split is two schemas, not one filtered schema** (Stage 3.3, 2026-08-25)
`QuoteOptionClientRead` has no field for cost, margin, supplier payments, the agent cover fee
or the warning list, and `QuoteOptionInternalRead` extends it. A model that cannot represent a
cost cannot leak one, however the document template changes later — whereas a single model
filtered at render time leaks the first time someone adds a field and forgets the filter. The
existing `_keys_everywhere` leak test now covers the option endpoint too.

**Stage 2 rate selection got an explicit occupancy tiebreak** (Stage 3.3, 2026-08-25)
Adding `occupancy` to rate identity in 3.1b left `AccommodationRateService.select_rate`
ordering by `effective_from` alone, so as soon as a real sheet's Single and Double rows were
loaded, several rows shared an `effective_from` and the row returned was whatever the planner
produced — the same quote could price two ways on two runs. It now breaks the tie on the
**highest** occupancy: a room selected without a headcount is the room as the hotel sells it,
and a double is not priced as a single. Occupancy-aware selection for a group lives in
`OptionPricingService`, not here.

**A "BnB option" is one that needs a chef, not one with a category label** (Stage 3.4, 2026-08-25)
The quote shape is "3-9 hotels plus 1-2 BnB options", which needs the split to be decidable.
`accommodations.category` is free text an admin typed, so counting on it would be counting on
a convention nothing enforces. The split is taken instead from whether pricing had to add a
chef: an option resolved onto a plan that leaves the guests to feed themselves is
self-catering. That is derived from the rates rather than from a label, and it is exactly the
commercial difference the split is about. All four bounds live in pricing config.

**Readiness is graded, not a boolean** (Stage 3.4, 2026-08-25)
A quote can be wrong — an unpriced option, a bed-and-breakfast option with no chef cost, no
recommendation, two recommendations — or merely thin, two hotels where five would sell better.
One flag would either let an under-priced quote out or refuse a perfectly correct one. So
problems carry a severity: blocking stops issuing, advisory is returned alongside. Issuing
reports *every* blocking problem at once, because fixing them one 400 at a time is how the
second one ends up in the client's copy.

**Engine and agent refusals are told apart by column** (Stage 3.4, migration `15c4d3d4af6b`)
Re-pricing rewrites the refusals the engine derives from the rates. An agent's typed refusal —
the reference document's "Diani Cottages, caps at 16 guests" — is not rediscoverable from any
rate, so the 3.3 implementation would have erased it on the next re-price. A NULL
`accommodation_id` is not a usable discriminator: a manual refusal may well name a property we
hold. Hence `source`, and an engine refusal cannot be deleted through the API — deleting it
would only hide it until the next re-price.

**A version's internal cost is what we pay, not the costed subtotal** (Stage 3.4, 2026-08-25)
On a discounted rack rate the two differ by the retained half. Calling the costed figure
"cost" would understate realised margin by exactly that amount, which is the number the
business is run on. So `internal_cost = cost_subtotal - retained_discount`, and margin on the
version comes out as profit + contingency + retained half — the three figures §3.5 insists on
tracking apart, correctly added back together.

**An issued quote refuses assembly edits** (Stage 3.4, 2026-08-25)
Versions are immutable, but the quote they hang off is not. An option added after the client
received the document would make the stored version disagree with what they are looking at,
and the disagreement would be invisible from either side. Re-issuing is the supported path: it
appends a new version and leaves the old one readable.

**The Stage 3 pricing config was configurable in name only** (Stage 3.4, 2026-08-25)
`profit_pct`, `contingency_pct`, `per_person_rounding` and `quotation_validity_days` were
added to the config model in 3.3 but never to the read/update schemas, so no admin could see
or change them through the API — the exact thing the design doc requires them to be ("in
pricing config, not hard-coded, with a per-quote override for the exception case"). A value in
a settings model that no endpoint exposes is a constant with extra steps.

**Every collection listing is ordered** (Stage 3.4, 2026-08-25)
`CRUDService.list()` applied a LIMIT with no ORDER BY, which is unordered in Postgres: which
rows come back can change between runs for reasons nothing in the application controls. Once a
table held more rows than the page, a freshly created record could be absent from the listing
meant to show it — which is how it was found, when a Stage 2.7 test that creates a client and
then looks it up started failing the day the suite crossed 200 clients. Listings are now
newest-first on `created_at`, falling back to the UUIDv7 primary key, which is time-ordered
and sorts the same way. The fix is in the shared base class, so it applies to every reference
and catalogue endpoint at once rather than to the one that happened to break.

**The document renders only from an issued version** (Stage 3.5, 2026-08-25)
There is deliberately no endpoint that renders an unissued quote. The version *is* the
document: rendering live rates would produce a proposal whose figures move between reloads,
which is the exact failure immutable versions exist to prevent. Passing a version number
renders an earlier one as the client received it. The single exception is imagery, read live —
a photograph is presentation, not terms, and freezing image ids would leave an old document
unable to show a picture that had merely been re-cropped.

**The view model is the internal/client boundary, tested against the rendered bytes**
(Stage 3.5, 2026-08-25)
`QuotationView` has no field for cost, margin, supplier payments, contingency, profit or the
agent cover fee, so no template edit can print one. The two-schema split is the mechanism, but
the test asserts against the *rendered page*: the quote it uses carries a discounted rack rate
on purpose, so every internal figure it produces is a different number from the client's, and
any one of them appearing shows up as a failure.

**A font stack is charset-validated, not HTML-escaped** (Stage 3.5, 2026-08-25)
Autoescaping turned a quoted font name into `&#39;...&#39;` inside the stylesheet — invalid
CSS that silently drops the face, which is the sort of failure nobody notices until a client
comments on the typography. Escaping cannot be the answer, so the two font values are
restricted to a font-stack charset (no braces, semicolons, angle brackets, parentheses,
slashes or at-signs) and emitted unescaped. A test asserts every `font-family` in the rendered
document resolves through one of the two custom properties, so the eventual swap to the real
brand faces stays a two-line change.

**Standing copy is configuration** (Stage 3.5, 2026-08-25)
The wordmark, contact details, "why us" list, availability notice, closing disclaimer, VAT
note, tagline and page size live in `app_settings["document"]`, not in template literals. A
hard-coded phone number on a client-facing document is a support ticket waiting to happen, and
the notices are commercial language sales and finance will want to reword without a deploy.

**A section with no data is omitted, not filled** (Stage 3.5, 2026-08-25)
The transport page needs transport segments; the signature-experience page needs an activity
flagged for its own section. Rendering them from assumptions would put a description of
transfers the client is not getting onto a priced proposal, which is worse than saying nothing.
The same instinct drove a layout fix found by looking at the printed pages: cell borders moved
from the grid container onto the cells, so five facts in a three-column grid stop after five
rather than drawing an empty sixth box that reads as a missing value.

**Image cropping is CSS, not a stored derivative** (Stage 3.5, 2026-08-25)
The design calls for images centre-cropped to the template's aspect ratios. A fixed aspect box
with `object-fit: cover` *is* a centre crop and renders identically in print, so originals are
kept and cropping stays a presentation concern. Storing pre-cropped copies would mean
re-deriving every image whenever a layout changed, for a result the renderer produces free. A
stored derivative earns its place only when one image needs a crop of its own — a subject
off-centre — which is a different problem.

**PDF rendering sits behind a provider seam, with headless Chromium behind it**
(Stage 3.6, 2026-08-25)
The template was designed and visually verified in a browser, and CSS grid, `object-fit`
(which is how the document centre-crops its photographs) and `@page` all behave there. A
pure-Python engine such as WeasyPrint needs no subprocess but does not implement grid, so it
would silently reflow every page of this template — plugging one in is easy, making the
document survive it is a different job. A hosted rendering API would plug in at the same
seam, which is what a container without a browser would reach for. The protocol is
deliberately narrow, HTML in and PDF bytes out, so nothing about the quotation leaks into the
renderer.

**A configured browser path is never second-guessed** (Stage 3.6, 2026-08-25)
When `PDF_BROWSER_PATH` is set and wrong, the renderer reports itself unavailable rather than
falling back to whatever else it can find. Two engines paginate differently, and a client
proposal changing shape because a host happened to have Edge installed instead of Chrome is
the kind of difference nobody would think to look for. Discovery only applies when no path was
given.

**Missing renderer and broken renderer are different errors** (Stage 3.6, 2026-08-25)
No browser on the host produces a message naming what to install and pointing at the HTML
document, which still renders — that is the whole reason for saying it. A browser that ran and
failed reports the engine name and its own output. Neither is a 500, because both are things
an operator can act on and a caller needs to distinguish.

**The PDF is deliberately not cached** (Stage 3.6, 2026-08-25)
Caching would have to key on the version *and* on the brand copy, the fonts and the paper
size, all of which an admin can edit — so a version-keyed cache would keep serving the old
phone number after someone corrected it. Paying about a second per render is cheaper than that
class of bug. If PDFs later need attaching to email, they can be stored at that point,
fingerprinted against the configuration they were produced from.

**Documents are self-contained, which fixed the HTML too** (Stage 3.6, 2026-08-25)
The 3.5 note called linked images "a PDF problem". It was wider than that: a browser opening
the HTML document does not replay a bearer token when fetching an `<img>` either, so the
linked version had broken images in every context that mattered. Images are now inlined as
data URIs by default in both outputs, which also makes the HTML something that can be saved
and forwarded. `?inline_assets=false` keeps links for a preview whose fetcher can
authenticate.

**Uploaded images are decoded before they are accepted** (Stage 3.6, 2026-08-25)
The declared content type is a claim the uploader makes; decoding is the check. A corrupt file
used to upload, store and embed without complaint and then render as alt text across the hero
of a client proposal — which is how it was found, by looking at a printed cover that was a
dark rectangle with a caption on it. The format check runs before the decode so the message
matches the mistake: a PDF in an image slot is told it is the wrong format, not that it failed
to decode. Width and height fall out of the same decode, so those columns stop being
permanently NULL.

**VAT is normalised at ingestion, and the column records provenance rather than state**
(Stage 3.7, 2026-08-25)
§3.2 has said since the design was written that stored rates are always VAT-inclusive and
that an exclusive source is grossed up ×1.16 on the way in. The code stored what it was
given and set `vat_inclusive` to match, so a sheet marked exclusive was kept exclusive —
and because the engine deliberately adds no tax anywhere, nothing downstream ever made up
the difference. Every quote off such a sheet under-charged by the whole VAT rate while the
document told the client the price included it. The gross-up now happens once, in
`app/core/vat.py`, at both doors a rate can arrive by (a confirmed supplier document and a
hand-entered rate), and the stored `vat_inclusive` is true by construction. Normalising at
write time rather than read time is the point: a gross-up applied at pricing time is a rule
five call sites have to remember, and the failure when one forgets is a silent 16%
under-charge. `to_vat_inclusive` is idempotent, so re-confirming a sheet cannot tax it twice.

**Occupancy had to be on the manual rate schemas, not just the ingested ones**
(Stage 3.7, 2026-08-25)
3.1b put `occupancy` into the rate table and into its uniqueness key, and 3.2 exposed it on
the ingestion confirm step — but the hand-entry create schema never gained it, along with
`rate_kind`, `supplier_discount_pct` and the VAT basis. The read schema omitted them too, so
the admin could not even see which occupancy a rate belonged to. The practical effect was
that a property typed in by hand could hold exactly one rate per room/plan/residence/season
and could therefore never be priced for a lone guest — the ordinary odd-room case. Found by
writing 3.7's VAT tests and discovering there was no way to submit a VAT basis at all.

**A deduplicated upload still applies its flags** (Stage 3.7, 2026-08-25)
Content-addressed storage returns the existing row when the same photograph is uploaded
again, which is right — a gallery upload of five files where two repeat should not
half-fail. But returning it *untouched* made "I already uploaded this, now make it the
cover" a silent no-op: 201, the correct row, and nothing changed. Flags from the repeat
upload are now applied, and only `True` promotes: an upload that says nothing about the hero
is not asking to demote the current one either.

**Test fixtures must not produce identical bytes across runs** (Stage 3.7, 2026-08-25)
The image fixtures used a fixed palette, so a second run against the same throwaway database
deduplicated onto the *previous* run's rows and inherited whatever hero flag they had ended
up with. Two document tests passed on a fresh database and failed on a re-run — the kind of
flake that gets a test deleted rather than fixed. Each generated PNG now carries a nonce in
its metadata, so the pixels an assertion looks at are unchanged and the bytes are unique. The
suite now passes twice in a row against a dirty database, which is the actual property worth
having.

**Rooming cohorts and charging cohorts are different partitions** (Stage 3.8, 2026-08-25)
Mixed groups need both, and applying either partition to the other job is expensive.
**Rooms split by residency only**: a room is priced per room at one residency, so a resident
and a non-resident cannot share one without leaving the room's rate undefined. **Charges
split by residency and traveller type**: a child pays a child rate, but a child still sleeps
in their parents' room — partition rooms by traveller type as well and a family of two adults
and two children needs four rooms instead of one. The cost of per-residency rooming is the
occasional extra room: three residents and three non-residents need four twins where six of
one residency need three. The obvious examples hide this (25 people and 7 people give 13 and
4 under either rule), which is why it is written down and has a test of its own.

**A shared cost is split before it is converted** (Stage 3.8, 2026-08-25)
A coach chartered for a mixed group is one amount in one currency whose per-head share has to
land in two — shillings for the residents, dollars for the non-residents. The share is
computed first, in the line's own currency, and only then converted. Splitting a converted
total instead would give each cohort its own rounding of the exchange rate, so the same quote
could price a cent differently between runs. Exact equality cannot survive a round trip
through a non-terminating rate (15,000 ÷ 130), so the test asserts sub-cent drift rather than
pretending otherwise.

**Shared costs sum exactly, with the last cohort taking the remainder** (Stage 3.8,
2026-08-25)
Allocating a shared cost by exact division and accepting the drift would leave the cohort
totals adding up to something other than the cost — and a document whose parts do not sum to
its whole is the specific failure this design exists to remove. Which cohort absorbs the
remainder is deterministic rather than arbitrary, so re-pricing cannot move a shilling
between cohorts.

**Per-person rounding is a real source of margin on a large group** (Stage 3.8, 2026-08-25)
Rounding up to the nearest 100 is applied per person and then multiplied by the headcount, so
the bound on a cohort is one rounding step *per traveller*, not per cohort. A 25-person
booking can therefore carry up to 2,500 of rounding above cost. That is also the answer to
"why is the total 447,500 when the cost is 447,237". Worth knowing deliberately rather than
discovering during a reconciliation.

**Accommodation arrives at the vector pre-totalled** (Stage 3.8, 2026-08-25)
Every cost is an `(amount, currency, basis)` triple resolved against the group — except
accommodation, which enters as a `per_group` figure already totalled for its residency. Rate
selection across occupancies, seasons and room types is the pricing service's job, and
re-deriving it inside the basis layer would mean two implementations of the same rule. The
basis table is where a new charging shape gets taught; it is not a second rate engine.

**`compute_park_fee` had no callers** (found 2026-08-25)
Its docstring claimed the Stage 2.8 pricing engine reused it. Nothing in the codebase called
it, so park and conservation fees were not merely absent from Stage 3's option build-up —
they were computed nowhere at all. Recorded because the docstring was actively misleading:
the gap looked like a Stage 3 omission and was system-wide.

**A fee's currency belongs to the schedule column, not to the residence category**
(Stage 3.8, 2026-08-25)
The park-fee seeder first took each row's currency from the residence category's
`default_currency_code`, and stored Kenya Resident fees in **dollars** — the category's
seeded default — where the KWS schedule charges them 2,025 *shillings*. Two different facts
had been conflated: a category's default currency is what we would choose to **quote** that
traveller in, while a fee's currency is what the authority **charges**, and they genuinely
differ (KWS bills a Kenya Resident in KES; Swahili Beach's STO sheet quotes the same person in
USD). Reading it off the source column is also what keeps a stored fee reconcilable against
the PDF it came from. Cohort pricing converts between cost currency and billing currency, so
nothing downstream needs them to agree.

**Seeding real reference data must be able to correct itself** (Stage 3.8, 2026-08-25)
The seeder was insert-only, on the principle that a new schedule supersedes by adding rows at
a later `effective_from` and never rewrites published history. That principle is right and it
does not cover a **transcription error in a row the seeder owns**: the wrong figure is already
on file, so every subsequent run skips it and the error is permanent. Found exactly that way —
a fee stored in the wrong currency, with a re-run declining to fix it. `_fee` now updates a row
whose figures differ from the schedule and reports a `fees_corrected` count, so a non-zero
count on a real run is a signal worth reading. Superseding and correcting stay distinct
operations.

**Two residence categories were missing or wrong** (Stage 3.8, 2026-08-25)
The KWS schedule prices four columns: East African Citizen and Kenya Resident in KES,
Non-Resident and African Citizen in USD. `african_citizen` — a national of an African country
outside East Africa — had no category at all, so every such traveller was quoted as a full
non-resident (Amboseli: USD 90 against 50). `resident` was named "Resident" and defaulted to
USD. Both corrected in the seed defaults; note that seeded reference data does not propagate
to an existing database, which is why the fee currency no longer depends on it.

**Child age bounds differ per park AND per residence category** (Stage 3.8, 2026-08-25)
Already modelled per fee row, and the real data justifies it more strongly than the original
reasoning did. KWS defines a child as five-to-under-eighteen but exempts a child of five and
under, so the fee-bearing band is 6–17. The Maasai Mara charges a citizen child from 3 and a
non-resident child only from 9 — different bounds for the same park on the same day. An
eight-year-old non-resident is therefore free where a citizen of the same age is charged.

**KWS's MICE group ladder is ambiguous and is shipped switched off** (Stage 3.8, 2026-08-25)
The schedule reads "Amount of fees: 30% of the applicable park entry fees" for a 100+ group,
down to 5% for a 10–29 group. Read literally the group *pays* that percentage — which would
make a small group's deal far better than a large one's and inverts the ladder, so the only
monotonic reading is a *discount* of that percentage. `MICE_LADDER` models the discount
reading and `mice_discount_pct` returns zero unless a ladder is supplied, because the two
readings differ by an order of magnitude and the safe error is the visible one: failing to
claim a discount shows up as a slightly high quote, while applying a 95% reduction we are not
owed is a loss nobody notices. Enable it once KWS confirms.

**Park fees are per park category; conservancy fees are per night** (Stage 3.8, 2026-08-25)
KWS prices by tier — Amboseli and Lake Nakuru are one "Premium Parks" line — so the seeder
expands a category across its parks, since a quote names a place rather than a tier. Two other
shapes the real data revealed: Mara conservancies charge **per person per night** where park
entry is **per person per day** (which is why the basis table separates nights from days), and
Lewa charges a different figure for a day visitor than for an overnight guest. Also recorded
but not charged yet: the KWS vehicle seat-band levy, 4,500 a day for a 25–44 seater, which
lands on any quote that drives into a park and is exactly the line that gets forgotten.

**The brand faces are embedded, not linked** (Stage 3.11, 2026-08-25)
Cormorant Garamond and Libre Franklin arrived from the client, closing §9's first open
question. They are committed to the repo and inlined as data URIs rather than pulled from
Google Fonts, because the print path is where a linked face fails worst: the PDF renderer
opens a local `file://` page in headless Chromium, and a font request that does not resolve
still produces a document — in a fallback face, at different metrics, with nothing raised.
Same reasoning as the photographs in 3.6. A missing file is skipped rather than fatal (a
document in the wrong face beats no document), and `missing_faces()` exists because that
failure is invisible by nature.

**Three font files, not nine** (Stage 3.11, 2026-08-25)
Downloading the five Cormorant weights the client listed produced five byte-identical files.
Both families are variable fonts, so one file per style covers the whole declared range —
400–700 for Cormorant, 300–700 for Libre Franklin. 112 KB instead of 302 KB, embedded in
every rendered document, and every intermediate weight works rather than snapping to the
five that happened to be requested.

**The "no font may be named outside the two variables" rule paid for itself**
(Stage 3.11, 2026-08-25)
Swapping placeholders for the real faces was a two-line edit to `DocumentConfig`, because
3.5 forbade the template from naming a typeface anywhere except two CSS custom properties and
a test enforced it. That test needed one amendment — `@font-face` legitimately names
families — which is the distinction between declaring a face and applying one.

**The client's type scale overflowed three pages, and the space came out of imagery**
(Stage 3.11, 2026-08-25)
The specified sizes are substantially larger than the template was built for: a 52.5pt cover
headline against 30pt, 33pt property names against 26pt. Applying them pushed the running
footer off three pages, each of which then took a sheet of its own carrying nothing but that
footer. The fix was to give the space back from the option pages' imagery (hero 52→44mm,
gallery 26→22mm) and from leading that was too loose for display type at these sizes — the
closing tagline was set at 1.7, which is body leading. The type the client specified was not
touched.

Found by rendering and measuring rather than by reading the CSS: an ink-extent check over the
rasterised pages showed content ending at 91% of a page whose safe area stops at 87%. The
page count is asserted exactly, not bounded, because a silent extra page is precisely this
bug — a sheet with nothing on it but the footer.

**px at 96dpi is the right reading of the client's spec** (Stage 3.11, 2026-08-25)
The scale arrived in px with no artboard size stated, which is ambiguous: the same 70px is a
different size on a 794px A4 artboard than on a 1240px one. Body text at 14px settles it —
14px is exactly 10.5pt at 96dpi, which is the print convention the template already used and
an unlikely coincidence. Every value is therefore the client's figure x 0.75, and the
derivation is recorded beside the scale so it can be re-checked rather than re-guessed.

**A rack row and its NETT twin are one rate, not two** (Stage 3.12, 2026-08-29)
The most valuable thing the rate importer does. Real sheets publish both figures and agents
transcribe both, so a room-night arrives as two rows: "450, rack" and "360, sto — Published
Agent NETT = rack less 20%". In the client's 3,161-row workbook that is **649 room-nights**,
and the `discount_percent` column was blank on every one of them because the sheets state the
concession in prose.

They are not duplicates, and neither row alone is right. The rack row alone quotes the client
450 and believes we pay 450, discarding the whole concession from margin. The NETT row alone
costs the client 360 and hands them all of it. §3.5 already models this as one row — rack plus
a percentage — so the pair is collapsed into exactly that, with the percentage derived as
`1 - nett/rack`. The derived figure is round-tripped against the published NETT and any
penny-level disagreement is reported rather than absorbed.

Anything that is not a clean rack/NETT pair — three rates for one room-night, two rack rows,
37 groups in this corpus — is a conflict the importer must not resolve by picking one. Those
are reported and left out.

**Date order is decided from the file, never assumed** (Stage 3.12, 2026-08-29)
`11/01/2027` is a valid date under both readings and the wrong one prices April at March
rates without complaining. Only a component above 12 carries information, so the importer
counts those across the whole sheet before parsing anything: 2,820 of the client's dates are
day-first and none month-first, which settles it. A sheet containing both readings imports
*nothing* — every date in it is suspect. A sheet where no date has a component above 12 is
genuinely ambiguous and says so.

**The importer refuses to invent, and defaults only labels** (Stage 3.12, 2026-08-29)
A row missing its validity window, occupancy, room type or meal plan is rejected and
reported, never defaulted: a guessed season window is a price the supplier never quoted and
it would price real quotes. 200 of 3,161 rows fail this way, concentrated in three properties.
The one thing defaulted is a rate's season *name*, because "Standard" is a label rather than a
figure — but a supplement's label is **required**, since that text is what a client reads, and
defaulting it both printed "Standard" on a proposal and silently collapsed 43 distinct extras
onto one natural key.

**Rejections are counted and reported by property, not by row number** (Stage 3.12,
2026-08-29)
Two reporting bugs found by reading my own output. `accepted` counted *problems* rather than
rows — a row missing both dates yields two — which overstated the damage by a third. And a
list of row numbers spanning nine properties is not actionable: nobody fixes "row 1039", they
fix a rate sheet. Rejections now carry the property name and are grouped by it.

**room_sleeps and price_covers are both lower bounds on capacity** (Stage 3.12, 2026-08-29)
The obvious rule — trust `room_sleeps` where stated — is wrong against real data: in the
client's workbook that column mirrors `price_covers` row by row (the single row says 1, the
double row of the same room says 2) rather than stating the room's capacity. Treating either
as authoritative produced two dozen false conflicts on one property. A room priced for two
guests sleeps at least two; that is all either column proves, so the maximum across both is
the honest floor. Erring low is the safe direction, since too small a capacity books *more*
rooms and over-quotes visibly where too large under-quotes silently.

**Filled-in workbooks are gitignored** (Stage 3.12, 2026-08-29)
The blank template and its guide are tracked; anything an agent has typed supplier rates into
is confidential and must never enter git history — the same rule as the uploaded sheets. The
importer is verified against a throwaway database (`tours_intake_test`) rather than the suite's
`tours_test`, so 2,800 real rates cannot pollute the fixtures every test depends on.

**Currency belongs in the accommodation rate uniqueness key** (Stage 3.12, 2026-09-02)
A rate card may publish the *same* room-night in several currencies and expect the agent to
bill in whichever the client is invoiced in. Kobe Suite Resort does this for every night:
19,674 KES / 197 USD / 179 EUR for one Standard Garden View Suite — three rows that are one
price quoted three ways, not three prices. The old key
`(room_type, meal_plan, residence, occupancy, effective_from)` read them as a collision, so
which currency survived depended on row order in a spreadsheet, and the survivor could be the
EUR figure for which no exchange rate exists — leaving the property unpriceable while a usable
USD figure sat in the sheet, discarded. Keeping all three also lets the pricing engine prefer
the presentation currency and drop an FX conversion, and its rounding, out of the quote
entirely. Migration `8c1d2a9b4e37`. This is what took the client's second workbook from 41
unresolved conflicts to 2.

**A distinction the schema cannot express keeps the higher figure, loudly** (Stage 3.12,
2026-09-02)
One Stop Nanyuki charges 10,000 Sunday–Thursday and 13,500 Friday–Saturday for the same hut;
Soames does the same. The sheets draw the distinction in the `label` column, and there is no
weekday mask on `accommodation_rates` to honour it. Three options, and none of them is
"resolve it":

- *Drop the rows* — the property becomes unquotable, which is a real loss for a correct sheet.
- *Keep the first* — depends on spreadsheet row order. At One Stop that is the cheaper figure,
  so every weekend stay under-charges by 35% with nothing to show it happened.
- *Keep the higher, and report it* — a weeknight over-quotes visibly, where the agent can see
  the figure and correct it against the sheet.

The third, on the same reasoning as capacity inference: an error the agent can see beats one
they cannot. Reported under its own heading rather than folded into `warnings`, because it is
a schema gap to close, not a data problem to fix. Two rows sharing a label are *not* this case
and stay conflicts — The One Watamu Bay prices one room-night at 13,500 per person and 27,000
per room for a single guest, a factor of two with nothing to choose between them.

**A blank `row_type` means `RATE`, decided in one place** (Stage 3.12, 2026-09-02)
All 64 Temple Point rows in the client's audited workbook arrived with an empty first column —
the common typo, since it is the one column that never varies. A row carrying a room, a meal
plan and an amount is unambiguously a rate, so defaulting is safe. The bug was that the two
passes over a sheet each decided this for themselves and *disagreed*: the write pass defaulted
to `RATE`, the capacity pass tested `!= "RATE"` and skipped the row. A whole property's rates
therefore imported while its room capacities were inferred from an empty set, falling back to
two guests per room. `N.row_kind()` is the single place that decides, which is the general fix:
a default that appears twice will eventually appear twice differently.

**A blank VAT column is read as inclusive, and counted** (Stage 3.12, 2026-09-02)
1,335 rows — 45% of the client's corpus, including all 720 Swahili Beach rows — state no VAT
position at all, and the client's own audit re-read the sources and confirmed none is
recoverable. Client decision: treat them as inclusive for now. That is the right call for
Kenyan hotel sheets, which are inclusive by convention, and picking the opposite default only
moves the error from under-charging 16% to over-charging it.

What is *not* acceptable is that the assumption be invisible. `IntakeReport.vat_unstated`
counts the rows per property and the importer prints them under their own heading on every
run, so the size of the assumption is restated each time rather than being a default nobody
remembers choosing. If a property later confirms it is exclusive, that report is the list of
what to re-import.

**A meal plan must have a rate for every residency on the quote** (Stage 3.8, 2026-09-02)
Pricing an option now looks up rates for each residency in the group vector, which raises a
question a single-residency quote never had: what if a property prices non-residents on full
board and residents on bed and breakfast only? The available plans are therefore
**intersected** across residencies rather than unioned. Pricing each half of the group on the
plan its own sheet happens to offer would put two different holidays on one line of a
quotation and call them comparable. If no plan survives the intersection the property is left
off with an internal warning naming what each residency does have, because "we have no
non-resident rates loaded" is a statement about our data, not about the hotel (§3.3a).

**Where a room-night exists in several currencies, the presentation currency wins**
(Stage 3.8, 2026-09-02)
§3.12 stores all of a rate card's currencies. Selecting one is the pricing engine's job, and
the tiebreak runs currency first, then season: a rate quoted in the currency the client is
being invoiced in needs no FX conversion, so neither its rate risk nor its rounding reaches
the client's figure. Without this the winner was whichever row Postgres returned first, which
on the client's corpus could be the EUR row — for which there is no exchange rate on file at
all, making the property unpriceable while a usable USD figure sat beside it.

**`pax_count` outranks the traveller rows only when it says something they do not**
(Stage 3.8, 2026-09-02)
The first cut of `build_group` gave `pax_count` flat precedence, which broke a quote carrying
both: two named travellers (one adult, one child) beside `pax_count` of 2 flattened into "2
adults", read as uniform, and got a single per-person figure that a mixed group must not have.
So the headcount wins only where it *differs* from the number of rows — 25 people of whom two
are named is 25 travelling, and nobody has said what the other 23 are — and otherwise the rows
win, because they carry the adult/child split as well as the total. Caught by an existing
test, which is the argument for having asserted the mixed-group case as a number rather than
as behaviour.

**Park fees enter an option's price on the cohort path; the age-based path stays**
(Stage 3.8, 2026-09-02)
The gap recorded since Stage 2.8 was "`compute_park_fee` has no callers — park fees are
computed nowhere". Half right, and the half that was wrong matters: they *were* computed on
the leg-based `PricingEngine` path, but not in the Stage 3 multi-option build-up, which is
the one the client's document renders. Every safari option was quoted with the beds and none
of the entry. No test caught it because every property in the demo catalogue sits in Diani,
where nothing charges one.

`OptionPricingService._park_fees` now charges them per person per day, **selected per night**
for the same reason rates are (§3.1) — the Mara publishes two seasons, and one lookup for the
stay would charge a boundary-crossing booking entirely at the cheaper one.

`compute_park_fee` is kept, and is not redundant. It is the **age-based** path: each park
sets its own child band (the Mara exempts under-6s and charges 6–17; others use 3–11), so
classification has to be re-decided against each fee rather than once for the quote. The
cohort path has no ages and takes the agent's declared traveller type at face value, which
means **a cohort labelled `child` is charged the child fee even where the park would exempt
that age**. It errs toward over-charging — the visible direction — but it is not the published
rule, and closing it needs ages on the quote rather than a change to either function. The
module docstring said the engine reused `compute_park_fee`; it never did, and that has been
corrected rather than left as a comment nobody could trust.

A destination charging *nobody* is silent, because most beach properties are not in a park. A
destination charging *some* residencies and not others warns, because that gap silently
under-charges the ones it is missing — the shape of a half-transcribed KWS table.

**Per-cohort prices sit beside the whole-group build-up, not instead of it**
(Stage 3.8, 2026-09-02)
The client's requirement — "per person basis for residents and a different one for non
residents" — is now a row per cohort in that cohort's own billing currency, alongside a group
total. Two things about the shape are deliberate.

*The whole-group `build_up` is untouched.* It stays in the presentation currency and remains
the internal worksheet: cost subtotal, contingency, cost basis, profit, agent cover fee. Those
are all pre-rounding, so they reconcile with anything derived from them. Replacing them with a
sum of per-cohort build-ups would have moved every existing hand-worked figure by a rounding
step for no gain, and the cost side genuinely is one number for the booking.

*Each cohort's per-person figure is rounded up first and multiplied back out.* Same rule as
§3.6 and for the same reason: a client can check `per_person x headcount = total` on the page.
Rounding a cohort total and dividing would reproduce the reference proposal's own
contradiction, where page 6 quotes 28,800 per person against a 720,000 total implying 28,400.
Asserted per cohort, not just for the group.

What each line reaches is the design: **accommodation** names a residency and no traveller
type, so it is shared within that residency (a resident adult and a resident child sleep in
the same rooms off the same sheet); **park fees** name both, because a resident child pays the
resident child rate and nobody else shares that line; **supplements, chef and food** name
neither and split per head, since a chef costs the same whoever eats.

**Exchange rates are pinned per option, not looked up inside the arithmetic**
(Stage 3.8, 2026-09-02)
`price_group` takes a plain synchronous callable, so the pure layer cannot do I/O. Rather than
work around that, the service pre-fetches every pair the option needs and closes over the
table. That is the stronger design regardless: every figure on one option converts at the same
rate, where a lazy lookup would let a rate change between two cohorts of the same group and
the totals would then not reconcile with the per-person figures they came from. A pair that is
genuinely absent raises with both currencies named rather than defaulting to 1 — the failure
mode a missing rate deserves, since a silent 1.0 would quote dollars as shillings.

**An option is a package of legs; a single property is a package of one** (Stage 3.9,
2026-09-03)
The client asked for **2 or 3 destinations in a single 7–30 day trip**, which an option
carrying one `accommodation_id` cannot express. `quote_option_legs` holds an ordered set of
(destination, property, per-leg meal plan, date range), and `QuoteOption.accommodation_id`
stays as the single-leg shorthand with the legs taking precedence — the same precedence the
group vector uses over `pax_count`, so one place answers "what is this option?".

`_price_one` became `_price_leg` plus a thin orchestrator, so a one-hotel quote and a
three-destination trip run the *same* code and there is no second implementation to drift.
The legs are **summed, not compared**: they are one offer, not alternatives, and if any leg
cannot be priced the whole package is dropped, because half a trip is not something to put in
front of a client. `rooms_required` is the maximum across legs rather than the sum — legs are
sequential, so summing would book a room in Diani for a night spent in the Mara.

Meal plan is a per-leg choice, and an explicit one is **not** a fallback. A day out of the
hotel makes half board the right plan rather than a failure to secure full board, and the
document has to be able to tell those apart.

**Contiguity is blocking, and checked twice** (Stage 3.9, 2026-09-03)
A one-night gap between legs is a night the client has no bed; a one-night overlap is a night
paid for twice in two towns. Neither is visible on a finished document — the per-person figure
is exactly as plausible either way — so they cannot be warnings. Checked at **creation**,
because there is no reason to store an incoherent package, and again at **readiness**, because
a quote's arrival or departure can move afterwards and silently break a package that was
correct when built.

Two things deliberately *not* blocking. A repeated destination is a note: Nairobi at both ends
of a safari is the commonest itinerary in Kenya, and the reason to surface it at all is that
the other cause is a copied leg nobody re-pointed. And legs are ordered by `sequence`, never
by date — sorting by date would silently repair a mis-sequenced package into a valid-looking
one, hiding the mistake instead of reporting it.

Minimum stay changes meaning for a package. For a single property it drops the option from the
comparison and says so on the document (§3.3a); for a package it is blocking, because the
package is one offer and a leg that cannot be booked makes the whole thing unbookable rather
than shorter.

**Dropping `uq_quote_option_accommodation`** (Stage 3.9, 2026-09-03)
It meant "do not offer the same hotel twice", which stopped being expressible as a column pair:
two curated packages can legitimately share a property on one leg and differ on another —
Nairobi then Mara against Nairobi then Amboseli. The intent survives as a service check
comparing whole leg *sequences*, which is the thing that actually has to be distinct, and the
old single-property check still applies between two options that both have no legs.

**Transport is charged into every option, not beside them** (Stage 3.10, 2026-09-04)
It is the same journey whichever hotel the client picks, so it is priced once per quote and
added to each option's build-up. Outside the options, the cheapest bed would look like the
cheapest trip — and a client compares trips. It also keeps the comparison between options a
comparison of the beds, which is the only thing that actually differs between them.

**A movement with no tariff blocks; it is never priced at zero** (Stage 3.10, 2026-09-04)
Zero is the dangerous answer: on a finished document it is indistinguishable from a leg the
client is genuinely not being charged for, and the whole cost of that movement is then missing
from every option. So an unpriced movement is recorded and blocks at readiness. For the same
reason a segment naming no destination is refused at creation — every fare is keyed on a
destination, so there is nothing to price it from, and the agent is one field away from a
correct quote.

**A shortfall of movements is advice, not a refusal** (Stage 3.10, 2026-09-04)
A journey is one movement per transition plus arrival and departure (`legs + 1`), derived from
the package rather than trusted from what was typed. But a client arranging their own airport
run is a real case, so a shortfall is reported rather than refused. A segment on our own or a
hired vehicle satisfies the check outright: the fleet model charges per vehicle per day and
covers every movement at once, so counting legs alone would false-positive on the commonest
case — and that segment is not charged a transfer tariff either, or the same drive would be
billed twice.

**Flights are unpriceable, not merely unpriced** (Stage 3.10, 2026-09-04)
Heissal holds no ticketing licence, so `air` is not a tariff lookup that happens to be empty —
an empty lookup would one day be filled in and start selling something we cannot sell. A
flight segment is named on the itinerary, reported so the fare reaches the exclusions, and
never enters the money. The flight's name is client-facing: a client who is not told to book
their own ticket is a client who arrives without one.

**Each movement prices at its own date** (Stage 3.10, 2026-09-04)
`quote_transport_segments.travel_date` (migration `d5a3e81c60b9`, nullable, defaulting to the
quote's arrival date). Tariffs are effective-dated because fares move, so a return rail leg
after a revision is a different price from the outbound one; pricing a whole quote at one
instant under-charged it with nothing showing. The same reason accommodation is selected per
night rather than per stay (§3.1).

VAT on these two tables is normalised **on the way out**, not at ingestion like every other
rate: they are entered by hand and carry the flag, so the gross-up happens once in
`_tariff_for` rather than at five call sites that each have to remember it.

**An add-on is marked up like everything else** (Stage 3.10, 2026-09-04)
VVIP transport is quoted apart from the package so the options stay a comparison of the same
journey — but through the same build-up, contingency and margin included. An add-on offered at
cost is an add-on sold at a loss. The cost stays internal; the client sees the price.

A transfer tariff is keyed on its route, and one route is not another: town-to-terminus is not
terminus-to-hotel. Where the named route has no rate the nearest row is used and **said out
loud**, because a plausible figure for the wrong drive is the error nobody goes looking for.
