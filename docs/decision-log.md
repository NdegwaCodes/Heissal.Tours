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
