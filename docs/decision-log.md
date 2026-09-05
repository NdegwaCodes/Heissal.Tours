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

**An option is identified by its option id, never by its property** (Stage 3.11, 2026-09-04)
Two curated packages can share their lead hotel and differ on a later leg, so `accommodation_id`
stopped being a key at 3.9. Issuing used it to find the recommended option's headline money, to
map each costing back to its option row and to stamp the recommendation and sort order into the
snapshot — so a quote with two packages starting at the same property could take its headline
price and margin from the wrong one, and the mapping dict collapsed both onto a single row.

**What a version freezes now includes the itinerary and the journey** (Stage 3.11, 2026-09-04)
Legs (destination and property *by name*, room, board, nights), each cohort's per-person and
total in its own currency with the conversion rates used, the residence categories' display
names, the headcount pricing actually used, and the journey's movements. Denormalised for the
reason the rest of the snapshot is: a destination renamed or a rate superseded must not change
what a version says was quoted, and the rate on file today is not the rate the client was
quoted at.

The transport page previously read the quote's **live** segments, so a document already sent
could quietly start describing a different journey. It renders from the frozen version, and a
version issued before this change simply has no transport page — honest, and better than a page
of what the quote looks like today.

**The headcount is frozen, not read off `pax_count`** (Stage 3.11, 2026-09-04)
A quote given cohorts has no `pax_count` at all — the vector *is* the headcount (§3.8) — so
everything downstream that reached for the column read zero, and the client's proposal said
"0 participants" beside a price for four people. `build_group` is the one place that decides
who is travelling, so its answer is what the version records.

**What the document does with a package** (Stage 3.11, 2026-09-04)
A full-width itinerary table under the two columns, printed only past one leg: for a single
hotel the facts panel has already said it. The route, not another column, in the comparison
table's property cell — it is what tells two packages apart when they share a first property,
and a seventh column does not fit A4. Verified by rendering: the page overflowed until the
"Itinerary" fact cell (a third copy of the same route) went, the gallery strip was dropped from
package pages and their hero gave back 24mm. A table continued overleaf is a table nobody reads.

Per-cohort prices are a **stacked list in the price panel**, not a three-column table: that
column is .88fr of the page and two money columns leave the label three characters wide. Shown
only where there are two or more cohorts — one cohort would repeat the per-person figure printed
in large type immediately above it.

Repeated movements are **counted, not listed**: a rail return with its four mandatory transfers
is six movements and two distinct routes, and a page that prints "Terminus to hotel — Included"
four times reads as a bug rather than as thoroughness.

**The worksheet is a second view model and a second template, not the proposal with
cost columns switched on** (Stage 3.12, 2026-09-04)
`QuotationView` having no field for cost, margin or supplier payments is the mechanism that
makes the internal/client boundary structural (§2) — a template cannot print what it was never
handed. Adding the worksheet to it would dissolve exactly that. So: two view models, two
templates, two permissions (`quote:read` for the proposal, `quote:read_cost` for the sheet),
both reading the **same frozen version**, because a mirror that can disagree with the thing it
mirrors is not evidence of anything.

**Every cost line carries its basis, its multiplier and its source row** (Stage 3.12,
2026-09-04)
A cost you cannot trace to a document is a cost you cannot defend when a supplier invoices
something else. Accommodation lines are aggregated per rate row and occupancy rather than per
night — a three-night stay in thirteen rooms is two lines an operator can check, not
thirty-nine — and each names the table, the row, the rate kind, the season and the supplier
document behind it. Hand-entered costs (a chef fee, a food budget) say *"entered by hand"*:
that is the line nobody can check against anything, so it is the line that needs re-checking.

Three numbers per accommodation line, as §3.5 requires: the sheet rate (reconciles against the
PDF), what the property invoices (reconciles against the invoice) and what entered the client's
price (reconciles against the quote). On a discounted rack rate those are three different
figures, and realised margin is only honest when all three are kept.

**Optional upgrades are their own component on the worksheet** (Stage 3.12, 2026-09-04)
Filed under transport they sat above a subtotal that deliberately excludes them, and a ledger
whose lines do not add up to its own subtotal is worse than no ledger — it will be believed.
Caught by rendering the sheet and adding up the column. The journey itself is printed once at
the top rather than under every option, or a sheet would read as though it had been paid for
several times.

**"Paid to the properties", not "paid to suppliers"** (Stage 3.12, 2026-09-04)
The stored figure is the accommodation half only — it exists because a discounted rack rate
makes what we pay differ from what we charge — and labelling it as everything that leaves the
account would understate outgoings by the whole journey.

**The exclusions list is config, and the quote adds to it** (Stage 3.12, 2026-09-04)
A priced proposal that does not say what it excludes is the commonest cause of a dispute at
invoice time: one total reads as covering everything a holiday needs. The standing list
(insurance, airport taxes, personal expenses, tips) is `app_settings["document"]`, because it
is commercial policy and changes when what Heissal sells changes — not a deploy. On top of it
the quote's own facts: the flights we cannot ticket (§3.10) and the optional upgrades, named
with their price so "not included" cannot be read as "not available". Marked with dashes rather
than ticks — an exclusions list bulleted like an inclusions list is the one misreading this
section cannot afford.

**A per-person rounding step per currency** (Stage 3B, 2026-09-04, client-confirmed)
One global step of 100 was right for shillings and badly wrong for dollars. Against the client's
own rates, Pride Inn Diani's USD 135 per person became USD 200 (**+48.1%**) and Palm Garden's
USD 144 became USD 200 (+38.9%). That is not a rounding convention to a client, it is a
different quote, and it loses a booking without anyone learning why.

`per_person_rounding_by_currency` in the pricing config, falling back to `per_person_rounding`
for anything unlisted: **USD 1** as the client chose, EUR and GBP defaulted to 1 rather than
waiting to be discovered the same way, KES unchanged at 100. `PricingConfig.rounding_for()` is
a method because pricing rounds in three places — the option build-up, each cohort's own figure
and an optional add-on — and a fallback spelled out three times is one that will eventually
differ in one of them.

`price_group` now takes `rounding_step` as a number **or a callable**, and for a mixed group it
has to be the callable: each cohort is billed in its own currency (§3.8), so two steps apply
inside one price. Residents round to KES 17,600 while non-residents on the same quote round to
USD 352.

**Known, and not this commit's to fix:** for a mixed group the option's whole-group
`build_up.group_total` and the sum of the cohort totals differ by the rounding — 126,600 against
126,720 on the two-cohort case above — because each rounds up at a different level. Both appear
on the client document today, which is a contradiction the design exists to prevent (§3.6).

**What the client is billed is the sum of the cohort totals** (Stage 3B, 2026-09-04)
Found while adding the per-currency rounding step, and the rounding is what makes it visible.
Each cohort's per-person figure is rounded up **in its own currency** and multiplied back out,
so a mixed group billed KES 17,600 a head and USD 352 a head is billed exactly the sum of those
— 126,720 — while the whole-group build-up, which rounds once at a different level, says
126,600. Both were on the client document: cohort rows adding to one figure printed beside a
total saying another. A document whose parts do not sum to its whole is the specific failure
§3.6 exists to prevent, and it was reintroduced by the very mechanism meant to avoid it.

`OptionCosting.client_total` is now the cohort sum where a group was priced per cohort, and it
is what the client schema, the document and the version's `selling_total` and margin report —
the revenue figure has to be the one on the invoice. The whole-group `build_up` is untouched:
it is the internal worksheet, and the worksheet prints **both**, because the gap is something
an operator reconciling an invoice should see rather than rediscover.

**A stated child rate prices the room for the adults and the child as an extra bed**
(Stage 3B, 2026-09-04)
`accommodation_rates.child_rate` had been in the schema since §3.1 and nothing read it, so a
child was an ordinary occupant: counted into the rooming, given a share of a room, and charged
what an adult is charged. That was wrong in two directions at once — the group was quoted a
room it does not need (two adults and two children in twins came out as two rooms, when the
sheet is selling one room and two extra beds) and the children were charged an adult's share
instead of the figure the supplier publishes for them. A family quote was therefore both
over-priced and unreconcilable against its own sheet.

Now: where the sheet states a child rate, the rooms are priced for the travellers they were
quoted for and each child is charged its own rate per night. `Group.rooming` takes an
occupancy override and `CostLine` takes `bearers`, so the room reaches the adults it was priced
for and the children carry a line of their own — the same night can no longer be billed from
two directions.

Three deliberate limits. **All or nothing per residency**: a stay whose child rate covers four
nights of five falls back to the plain rule entirely, because pricing the children inside the
room for one night and beside it for the others is neither of the two things the sheet could
mean. **Silence is not a discount**: a property that publishes no child rate charges the child
as an adult, exactly as before. **Infants stay occupants**: no sheet in the corpus prices one,
and treating them as children would be inventing a rate.

**`is_mandatory` on an activity decides the treatment, not the scope** (Stage 3B, 2026-09-04)
Included excursions were the last cost outside the vector: `Activity.is_mandatory` had been on
the model since §3.1 and nothing charged it, so the reference proposal's Wasini Island day was
named on the document and paid for by nobody.

Built first as a destination-wide charge — every mandatory activity at a destination, on every
quote to it — on the reasoning that an agent then cannot forget one. Twenty-six existing tests
failed in unison, all of them Diani accommodation cases picking up the demo catalogue's dhow
cruise, and they were right: a beach quote must not silently buy twenty-five people a boat
trip. The scope is the agent's **selection** (`quote_legs.activities`, which already exists);
the flag says the client cannot decline it, so it is costed into the package and listed under
Included rather than offered beside it with a price. A charge that genuinely applies to every
visitor to a place is a park or conservancy fee, and those are `_park_fees`.

Priced once per person at the fare in force **on the day the agent scheduled it**, per cohort —
a resident child pays the resident child fare — and costed once for the quote and charged into
every option, like the journey (§3.10): an excursion does not change with the hotel. The
selection row's own adult/child counts are deliberately ignored; the group vector is the one
answer to who is travelling (§3.8), and a second headcount could only disagree with it.

**A trip has one more day than it has nights** (Stage 4.1, 2026-09-04)
The day-by-day programme is derivation rather than new data — legs already hold dates, movements
already hold travel dates, activity selections already hold a day number — and laying those on a
calendar is the only way to see whether they agree. Three rules fix its shape. Arrival and
departure are both days, so a 1–4 July booking is three nights and **four** days and the fourth
is the one with the flight home on it; counting days as nights loses it. A day belongs to the leg
that holds its **night**, which is the same convention that lets two contiguous legs share a date
without charging it twice (§3.9). And the words belong to the document: the pure layer names
board by its plan code, the viewmodel decides how a proposal phrases it.

Two consequences worth recording. The departure day says **checkout and nothing about meals** —
it has no night under it, so printing the last leg's basis would promise a lunch and a dinner
nobody bought, and printing "breakfast" instead is right only until a room-only leg makes it
wrong. And the day a package **changes hotels names both** properties: that day belongs to the
leg whose night it is, so without it the page reads as though the client woke up where they went
to bed, and the move is the one thing on a day-by-day they cannot work out for themselves.

**An undated movement appears on no day** (Stage 4.1, 2026-09-04)
Built the other way first, on the reasoning that an undated segment is *priced* at the arrival
date's tariff (§3.10) so the page should agree with the price. A rail return with its four
mandatory transfers showed what that means: all six movements piled onto day one, so the client's
page said the transfer home ran the day they landed. Caught by an existing 3.11 test counting how
often one label appears. An incomplete programme with an advisory against it is honest; a
confident wrong one is not — and which tariff a fare is picked at is a separate question from
which day a page claims.

**A day off the trip is refused at creation, and it is a mis-price** (Stage 4.1, 2026-09-04)
Excursion fares are selected by day number (§3.8) and transfer tariffs by travel date (§3.10),
which made the day numbers something priced against before anything checked them. An excursion on
day nine of a four-day trip takes its fare from a date the group is not in the country; a movement
dated a month out prices off a tariff window it will not be charged at. Both quote perfectly
cleanly, and nothing on the finished document shows either — so both are blocking, and both are
refused at creation where the agent can still fix them rather than only at readiness. The two
advisories (an unscheduled excursion, an undated movement) are presentation gaps: the price is
right and the programme is incomplete, which is a document to finish rather than a figure to fix.

**One programme page, for the recommended option** (Stage 4.1, 2026-09-04)
Every option carries its own frozen programme, because which day a client is in the Mara depends
on the package. Printing all of them is five near-identical pages of one journey, so the document
prints the recommended option's — the one it leads on everywhere else — and only where the trip
has a shape to describe: a journey, an excursion, or more than one property. A four-day beach stay
would otherwise produce "Diani, full board" four times over, and a proposal that pads is one a
client stops trusting on the figures too. The days stay frozen on the version either way, so a
later itinerary view can render them all.

**Road distances are hand-entered, not derived** (Stage 4.2, 2026-09-04, client-confirmed)
The catalogue has held latitude and longitude since Stage 1 and they cannot answer the question
the quote engine needs: Nairobi to the Maasai Mara is about 225 km straight and about 270 km
driven, and the drive time depends on the surface far more than on either figure. A routing API
would give the distance and still not know that the last 40 km wants a 4x4 after rain. The
client's operations team drives these roads, and confirmed they will state the vehicle
requirement per route — so `routes` holds the driven kilometres, the timed drive, the vehicle
types the road takes, and a free-text note, effective-dated because the seasonal fact is exactly
the one worth dating: the same road is a saloon drive in January and a 4x4 drive in April.

Directional and read either way round. Distance is symmetric and time roughly is, so a lookup
falls back to the reverse row **and says it did** on the worksheet; where a return genuinely
differs the operator enters the second row and it wins for that direction. Refusing to read a
row backwards would make every itinerary need its return typed twice, which is how a table stops
being kept up to date.

**The drive on our own vehicle was free** (Stage 4.2, 2026-09-04)
The hole §3.10 left. A hired transfer is priced from a tariff; a movement on our **own** vehicle
hit `if segment.vehicle_id is not None: continue`, on the reasoning that the Stage 2.8 fleet
model would cost it — and the Stage 2 model is not in the Stage 3 build-up at all. So an option
whose group is driven to the Mara in the company Land Cruiser carried the beds, the park fees and
**nothing** for the eight-hour drive: 11,075 missing from a quote whose accommodation was 60,000,
on the invented figures the tests use.

Now costed from the route: distance from the table, litres from the vehicle's consumption, price
from the pump-price table on the day it drives, plus a day of driver and running costs. Two lines
in two currencies, because fuel is bought where the pump price is recorded and the crew is paid in
the vehicle's own — converted once each, so no rate touches a figure that did not need one. The
arithmetic is `compute_transport_cost`, unchanged since Stage 2.8: litres times price is not
something to implement twice.

**A movement costs one day of its crew.** Not a fraction derived from the drive time — a
ten-hour drive to the Mara and a two-hour transfer both take the day, and neither leaves it free
for another job. A multi-day drive is entered as the movements it actually is, which is also how
the itinerary reads it.

**A vehicle the road does not take is blocking** (Stage 4.2, 2026-09-04)
The reason the column exists, and it is two failures at once: the quote is under-priced, because
the vehicle the trip needs costs more than the one it was costed on, and the drive cannot be run
as sold. A saloon and a Land Cruiser look identical on a proposal, and the second failure is
discovered on the road. The route's own note travels with the refusal — "impassable after heavy
rain" is why the requirement exists, and an agent reading only "takes a Land Cruiser" reads a
preference.

Silence is not compliance: a movement naming no vehicle at all fails a stated requirement rather
than passing it, because the alternative sends nothing up a road that needs a Cruiser and calls
it fine.

**The tariff tables got an API** (Stage 4.2, 2026-09-04)
Readiness has told operators to "load the fare before issuing" since §3.10, and until now the
only way to load one was to edit a seed script. A blocking message whose fix needs a developer is
not a fix. `POST /destinations/{id}/transfer-rates` and `.../transport-modes` close it; the
latter refuses `air` with the licence reason rather than a list of permitted values, because an
operator who reads "must be one of road, rail" will assume air is coming.

**Contiguity is not drivability** (Stage 4.3, 2026-09-04)
A package is contiguous by construction (§3.9) — every night has a bed, blocking — and that says
nothing whatever about the roads between the beds. Nairobi to Amboseli to the Mara and back is
perfectly contiguous and puts a twelve-hour drive on a day the document calls a transfer, arriving
at a park gate after it has shut. Nothing in the quote shows it: the price is right, the nights add
up, and the failure happens on the road. §4.2 put the distances on file; this is the pass that
reads the map.

**Every sequencing fault is advisory, and that is a decision.** Each one is a trip that can be sold
and a trip somebody should look at twice — and the agent may know the long day is exactly what the
client asked for. A blocking rule here would have the system arguing with the person who spoke to
them. The blocking rules in this area are about money or deliverability and live elsewhere: a
movement with no tariff (§3.10), a road the vehicle cannot take (§4.2).

**Nothing is reordered, only reported.** Packages are curated, not enumerated (§3.9). A shorter
ordering is named with its saving and the note says why it may be deliberate — a flight time, a
lodge's availability, a migration crossing — because none of those are facts this module holds.
Ends stay fixed: the first leg is where the client lands and the last is where they fly home from,
so only the middle is permuted, capped at seven legs because permutations are factorial.

**No ordering is recommended on the strength of roads we do not have.** A missing route contributes
nothing to a partial sum, so comparing a fully-known ordering against one missing two roads would
recommend the itinerary we know least about, every time. `order_km` returns `None` rather than a
short total, and the score keeps `unknown_hops` beside the figures so a total that is really a
floor says so.

**The score is not a single number.** Total kilometres, total hours, the longest single drive and
the unknown hops, kept apart: two thousand-kilometre itineraries are different trips if one of them
is a single fifteen-hour push, and a collapsed score would be comparable and useless. It is frozen
per option on the version and printed on the worksheet's option header beside the route, which is
where an operator is already comparing packages.

**"Too long to drive in a day" is configuration.** `max_drive_minutes_per_day`, ten hours by
default — a dawn start and an arrival before the gates shut. It is a commercial judgement about
what Heissal is willing to put in front of a client, not a fact about roads, and it is the kind
that changes the first time one complains.

**A cache bug the test caught:** the shorter-order search asks about roads the given itinerary
never uses, which is the whole point of it. The first version looked up only the drives already
sequenced, so every alternative ordering read as a road with no row on file and nothing was ever
suggested. Every pair among a package's destinations is now resolved once and cached across
options.

**Generated copy passes the same gate money does** (Stage 4.4, 2026-09-04)
The roadmap's last Stage 4 item is an "AI-generated narrative", and the client has not specified
what it is for, so it was built on the reading that carries least risk and most value: the
paragraph under a property on the option page, reviewed before it can reach anybody.

The rule is the one `rate_extraction` already applies to money — *nothing this produces is ever
written straight to the thing a client sees* — and the reason is the same. A wrong figure on a
proposal is a commercial incident; a confidently wrong sentence about a hotel is a smaller version
of it, and harder to spot because it reads well. So a provider produces a **draft**, an agent may
edit it, and approval is a separate act with its own permission (`narrative:approve`). A role that
may write copy can exist without being able to publish it, or the gate is decoration.

**The brief carries facts, never adjectives.** Name, category, destination, room types, and the
board bases the property has *rates* on — that last one because a description promising half board
we cannot sell costs a booking the moment a client asks for it. The single free-text input is the
agent's steer, since an agent who has visited the property knows the thing worth saying. A
provider handed free text writes about whatever it is handed.

**A draft says where it came from**: provider, model, and the brief it was given, stored beside the
text — §3.12's source strings applied to words. An agent's own writing takes the same path with
the provider `hand`, and a model draft a person edits becomes `stub+hand`. Not vanity: the
provenance decides who can be asked what a sentence meant, and after an edit the answer is the
editor.

**No provider ships, and that is the honest state.** No model is configured for this project, and
an HTTP client for a vendor nobody has chosen would be worse than a seam. The default refuses out
loud and names the alternative. A template stitching the brief into a sentence was considered and
rejected: "Coral Sands Resort is a resort in Diani offering full board" is the facts panel above
it, retyped, and it would go out on client documents looking like something nobody wrote. Better
nothing than filler — the option page reads fine without a paragraph.

**Approved copy is frozen into the version.** Caught while writing the tests: making replacement a
routine act means resolving the paragraph at render time would have an old proposal quietly
re-describing its hotels. The text now freezes at issue, exactly as the money and the days do, and
the live lookup remains only for versions issued before §4.4. Superseded rows are kept, which is
what lets the table answer why last year's description differed.

**No quote could be won or lost** (Stage 5.1, 2026-09-04)
`QUOTE_STATUSES` has listed `accepted`, `declined` and `expired` since Stage 2 and **nothing could
set any of them**. Every quote in the system was a draft or was sent, so the CRM's first question
— how many of the proposals we send become bookings — had no data and would have reported zero
forever. Same shape as the other reachability gaps this build keeps finding (`is_mandatory`,
`transport_segments`, the tariff tables): a column that exists with no way to fill it.

**Expiry is derived, never stored.** A quote is expired the moment somebody looks at it past its
validity date, not when a nightly job last ran. Storing it needs a clock and a scheduler and then
has two answers whenever the job is late — on the one report the business actually reads. So
`Quote.effective_status` computes it, every read path gets the same answer, and a list can never
show "sent" against a three-month-old proposal. `expired` is deliberately absent from the outcomes
a person can record: a calendar decides it.

**Accepting is accepting an option.** A quote offers three to nine of them (§3.7), so "the client
said yes" without saying yes to *what* leaves the revenue ambiguous and operations with nothing to
book. The option is required at acceptance unless one was already chosen through `/select` —
choosing and accepting stay separate events, because the gap between "they like the second one"
and "they signed" is worth measuring.

**An expired quote cannot be accepted, and the refusal names the fix.** It is the case where a
client returns to a six-week-old proposal at rates that have since moved; the honest answer is a
re-issue at today's prices. Declining an expired quote *is* allowed and worth recording: "they
went elsewhere" and "we let it lapse" are different losses and only the first has a reason
attached.

**What the funnel refuses to do.** Value is kept **per currency** — a single "total won" spanning
shillings and dollars is a figure with no meaning, and converting them would bake today's rate
into a report about last quarter (§3.8). The win rate excludes outstanding quotes, because one
nobody has answered is not a loss and counting it as one makes every rate look like a crisis in a
busy month. A lapsed quote sits in **outstanding rather than lost**: nobody said no, and its
pipeline value is a follow-up call. Time to decide is a **median**, since one quote accepted after
eight months would move a mean somewhere no quote has ever been. And "no data" reports as `null`
rather than zero, because "we have not decided anything yet" and "we lose everything" are
different facts.

**The report is filtered on when a quote was issued**, not when it was decided: a month's win rate
must not depend on the previous month's. And it needs no cost permission — selling values only are
the figures clients were shown, so a sales manager sees the funnel without seeing what things cost
us.

**`quote:record_outcome` is its own permission.** These two endpoints decide what the business
believes about itself, and a quote marked accepted by mistake is a booking somebody expects to
happen.

**The one figure worth building the stage for:** whether clients take the option we recommended.
`selected_option_id` has been on `quotes` since Stage 3.4 waiting for exactly this, and if the
number is low then the *Recommended* flag is not describing what clients want — which nothing else
in the system would ever have said.

**The sales stages are rows, not code** (Stage 5.2, 2026-09-04)
Heissal has not told us their pipeline stages, and a `CHECK` constraint listing mine would need a
migration the first time somebody wants "site inspection" between quoted and negotiating. So
`lead_stages` is reference data: ordered, renameable, with flags saying which stage means won and
which mean lost. A generic set (new → qualified → quoted → negotiating → won/lost) is seeded on
first use and is theirs to change.

Renaming is safe **by construction**: every report asks "which stage means won" rather than
comparing against a string it was compiled with, so "Won" can become "Booked and deposit paid"
without a figure moving. The `key` stays for seeding and tests; the `name` is what an agent sees.
Changing what a stage *means* (its won/lost flags) is deliberately not an edit — that changes
history already counted.

**A pipeline is history, not a status column.** `lead_stage_events` records every move, including
the arrival, and the arrival matters: without it the time spent in the entry stage is invisible.
"Eleven leads at quoted" is a number; "eleven at quoted, median nineteen days, four past a month"
is a morning's work. Backwards moves and reopening a closed lead are **allowed** — a deal cools, a
client comes back a year later — because a pipeline that only goes forwards is one where agents
park leads at a stage they have actually left, and then the counts describe nothing. The two
refusals are the ones that would corrupt a report: moving a lead to the stage it is already at
(a stage change that did not happen), and closing one as lost with no reason.

**A lead is never created without a next action.** Defaulted a few days out rather than demanded:
demanding one makes the form an obstacle while the phone is ringing, and leaving it empty is how a
lead dies. It is the single behaviour that decides whether a CRM survives a busy week — a lead
with nothing scheduled appears on no list, annoys nobody, and disappears — so the attention list
reports it **first**, ahead of anything merely overdue.

**Nothing is closed on a timer.** Staleness is reported against a threshold the caller sets, and
the message refuses to conclude: a honeymoon enquiry for next August is not cold at three weeks.
A system that closed leads on a clock would be deciding sales policy.

**A lead may precede a client**, so `client_id` is nullable and the contact fields sit on the
lead. An enquiry arrives as a name and a phone number; typing a client record for every call that
goes nowhere is how a CRM gets bypassed. What they want is loose text and loose dates for the same
reason — "somewhere on the coast in August, maybe six of us" is the enquiry as it actually arrives.

**`quotes.lead_id` is the join §5.1 was missing.** It makes a **source answerable for bookings**
rather than for activity: won over *all* leads from that source, not over the quoted ones,
because a channel producing twenty enquiries and two quotes is not a 100% channel just because
both quotes converted. Stated budgets are summed per currency and only while a lead is open —
adding a won lead's guess to the pipeline would count the same money twice, once as a guess here
and once as a price in §5.1's funnel.

**Sources are free text, normalised.** Trimmed, lower-cased, spaces and hyphens folded, so
"Walk in", "walk-in" and "walk_in" are one row in a report. Not an enum: sources multiply with
every campaign, and a lead refused because "instagram" is not on a list is a lead somebody files
under "other" — after which the report is worthless anyway.

**`lead:configure_pipeline` is separate from `lead:manage`.** An agent moves leads through the
pipeline; a manager decides what the pipeline is. Reordering the stages changes what every report
means.

**An accepted quote used to lead nowhere** (Stage 7.1, 2026-09-04)
§5.1 gave a quote an outcome; nothing followed it. A deal could be won in the system and
operations picked it up in a spreadsheet — no booking, no deposit, no schedule, no record of what
had been paid. Stage 7.1 is where it leads: `bookings`, `booking_instalments`, `payments`.

**A booking is made against a version, not a quote.** `quote_versions` holds the immutable
snapshot the client actually received (§3.4); the quote it hangs off keeps changing. So the
booking points at the version and invoices that version's own selling price — and there is a test
that re-prices the quote afterwards and asserts the booking's total does not move. A booking whose
figure could change is not a booking. The dates, headcount and currency are copied onto it for the
same reason: an operations screen showing different dates because somebody edited the quote would
be worse than useless.

**Creating one is an act, not a side effect of accepting.** Accepting records a sale; a booking is
operational. Folding them together would mean every acceptance produced a half-finished
operational record — and the gap between "they signed" and "we booked it" is exactly what an
operations queue is made of. One trip cannot be held twice, but a cancelled booking frees the
quote to be re-booked, because clients come back.

**The schedule is invoice lines, not a percentage.** "30% deposit" is policy; "KES 223,750 due on
4 September" is what a client pays and what a bank statement is reconciled against. The
percentages live in the pricing config (`deposit_pct`, `balance_due_days_before_travel`) and are
resolved to dated rows at the moment of booking — which is what freezes them: changing the deposit
rule next month must not restate an invoice already sent.

Three arithmetic rules, all tested without a database because these are the figures on an invoice
and every way they go wrong is silent. **Instalments sum to the total exactly** — the balance is
the *remainder*, not a second percentage, because two rounded parts of a rounded whole do not add
up to it and an invoice a cent out is one somebody writes off by hand. **A late booking is one
payment, not two**: where the balance date has passed the schedule collapses to a single payment
due today, since an invoice asking for a balance before the deposit gets a form ignored rather
than corrected. And **a balance never goes negative** — overpayments are real (clients round up,
pay twice), so the balance floors at zero and the credit is reported separately, because "you owe
minus four thousand shillings" is not a sentence a client should read.

**Payments are applied oldest first, not matched.** Real payments do not line up: a client sends a
round 100,000 against a 120,000 deposit, or pays two instalments at once. A system that insisted
on matching would leave an operator unable to record what the bank plainly shows.

**A payment in another currency is refused, not converted.** What cleared is a fact and the
exchange rate is a decision, and the decision belongs to whoever reconciles the statement. The
refusal says exactly that and asks for the amount that reached the account.

**Confirming happens on the deposit, not the balance**, because confirming is telling the
suppliers it is happening and that is what a deposit buys. Waiting for the balance would mean
nothing is confirmed until a fortnight before travel.

**No cancellation charge is computed anywhere.** The ladder — "inside 30 days, 50% retained" — is
commercial policy nobody has given us, and a plausible invented figure on a refund looks as though
it came from a contract. Cancelling records the reason and leaves what was owed and what was paid
exactly as they are, which is what the refund conversation actually needs. **This is the next
thing to ask the client for.**

**No payment integration yet, and the rows are the seam.** M-Pesa will land as `payments` rows
with their own reference, and an operator will still reconcile against a statement — a booking
that trusted a callback over a statement is a booking nobody can audit.

**`booking:record_payment` is its own permission**: recording money is the act every audit turns
on, and the person who books a trip is not always the person who reconciles the bank.

**The cohort order on a client document was database-dependent** (found by §7.1, fixed 2026-09-04)
`Group.cohorts` decides the order of the per-traveller rows on a proposal (§3.8) and is frozen into
the version in that order — so an unstable order means the same quote lists residents first today
and visitors first tomorrow. It was unstable: the relationship had no `order_by`, so the rows came
back in whatever order Postgres returned. Ordering by the primary key does **not** fix it either,
which is the part worth recording: `uuid7()` is a 48-bit millisecond timestamp plus ten random
bytes, so two rows inserted in the same millisecond sort arbitrarily. UUIDv7 is time-ordered
between milliseconds and unordered within one.

Fixed where the vector is built (`group._ordered`), which is the one place that answers "who is
travelling" (§3.8): the residency's own `sort_order`, then adult, child, infant. Stable *and*
meaningful — the order somebody would read them out in — and every figure downstream follows,
because they all read the vector.

Exposed by a full-suite run of the §7.1 booking tests: the new writes changed which rows landed in
the same millisecond, and a §3.12 worksheet test that asserts the cohort rows in order started
failing about one run in two. Worth noting how it surfaced — it had been latent since §3.8, and the
only reason it was ever visible is that one test asserts on order rather than on a set.

**A stage column was standing in for an activity log** (Stage 5.3, 2026-09-04)
§5.2 shipped with the limitation written into its own docstring: `Watched.stage_since` was
described as *"the closest thing to when somebody last did something without a full activity
log."* Stage 5.3 is that log — `communications`, one row per call, email, message, meeting or
internal note — and it exists because three questions a sales operation runs on were
unanswerable without it.

**When somebody last actually spoke to them.** An agent can call a client every week without
moving a stage, and a lead can be dragged across three stages while nobody has picked up the
phone. Staleness measured by stage movement was measuring the wrong thing; it is now measured
against the log, and the message says which of the two it means, because "at Quoted for 34 days"
and "not spoken to for 34 days" call for different actions.

**Whether the client is replying.** "Chased four times since 12 August, no reply" is the
temperature of a deal. A stage of *Negotiating* reports the opposite while being technically
true, which is the one place a pipeline reads as reassuring while a deal is dying.

**How fast the first reply went out.** In travel sales this is close to the whole game, and it
needs the arrival time and the first outbound word — there was no second half of that pair. The
pipeline report now carries a median beside a count of the enquiries that never got one: a fast
median over the answered half of an inbox says nothing about the half nobody opened, and folding
the unanswered ones in as zeros would hide exactly the enquiries worth finding.

**It is a log, not a mailbox.** Nothing sends anything. Sending mail from the platform means a
provider, a domain that passes SPF and DKIM, and a mailbox somebody actually reads the replies
in — none of which exists, and a half-built sender that silently fails to deliver a quotation is
worse than an agent using Gmail and typing what they sent. `external_ref` is the seam: a provider
message id lands there and the rows do not change. **Issuing a quotation deliberately does not
auto-log a "sent" entry either** — we do not know that it was sent, and an entry the system
invented is the same lie as one an agent got wrong.

**`occurred_at` is not `created_at`.** Almost everything here is written up after the fact,
between calls or on Friday afternoon. Every figure uses when it happened; measuring a response
time against when the notes were typed would flatter whoever writes them up promptly. The stamps
take the *later* of the two, so a Tuesday call logged on Friday cannot move "last contacted"
backwards past Thursday's email.

**An internal note is not contact.** Half of what gets logged is a note to ourselves — "her
sister is the one paying", "the villa is held until Friday". Recording those as outbound would
tell the attention rules the client had been contacted when nobody had, which is how an
unanswered enquiry hides on a dashboard. So there are three directions, and it is a CHECK
constraint: unlike a channel, there is no fourth direction a conversation can go.

**The channel is not an enum.** Channels multiply — a client moves to WhatsApp mid-enquiry, an
agent uses Instagram DMs — and an entry refused because "instagram" is not in a list is a call
nobody records. Normalised with the same fold as §5.2's lead sources, so one convention covers
both CRM modules.

**An unanswered call is a fact worth keeping.** "We have tried four times and never got them"
needs a different next step from "we spoke and they went quiet", so `reached` is recorded and
unreached calls are counted separately. A call that was not answered has no length, which is a
refusal in the rules and a CHECK constraint in the table — a nineteen-minute unanswered call is
the kind of nonsense that arrives through a script rather than through the API.

**It attaches to a lead, a client, a quote or a booking.** `subject`/`subject_id` as in §4.4's
narratives, not four nullable foreign keys. The talking does not stop when a lead is won: it
moves to the quote, then the booking, then the trip, and a log that only knew about leads would
lose every word exchanged about the trip that was actually sold. A lead's timeline therefore
gathers its client's, its quotes' and its bookings' entries. A call logged against a quote still
stamps that quote's lead, because an agent who logs it in the obvious place has not failed to
make the call.

**But a client-level call does not stamp a lead.** One client has many leads over the years, and
counting a call about this year's trip as contact on last year's dormant enquiry would make every
repeat client's old leads read as freshly spoken to.

**Nothing is deleted.** A wrong entry is amended — and the row says it was amended, though not
what it used to say: the question worth answering is "was the figure I am reading computed on
these words", and the stamp answers it. A history table for a history table is a cost with no
second payoff. An entry in the wrong place is **voided**: it stays visible, marked, with a
required reason, and counts towards nothing — not the last contact, not the response time, not
the chases. The call logged against the wrong client is still the record of what somebody
believed, and a vanished row leaves the next person wondering why a figure changed. The subject
and the direction cannot be amended at all, because those are facts about a different
conversation and two leads' figures were computed from them.

**`comm:amend` is its own permission**, and not "manage". Recording a call and rewriting the
record of one are different acts: an amendment moves the response times and chase counts an agent
is measured by, so a sales agent logs and reads and does not amend.

**Two stamps are denormalised onto `leads`.** `last_contact_at` and `last_inbound_at`, because
the morning list runs over every open lead and a correlated subquery per lead is what makes a
list slow enough to stop being opened. Allowed only because they are derivable:
`CommsService.recompute` rebuilds both from the log, and a test corrupts them the way a data
import would and proves the rebuild agrees with what incremental logging kept.

**A new first place on the morning list.** An enquiry nobody has answered at all now outranks
everything, including a missing next action. It is not a lead at risk — it is a customer already
lost and a reputation being spent — and with only a stage column, a lead nobody had replied to and
one somebody had spoken to twice were the same row. This changed one §5.2 test, which now logs a
call before asserting that the missing next action is the top reason.

**And, as in §5.2, it refuses to conclude.** Nothing here decides that a lead is dead or that
silence means no. Both silence thresholds (attempts, days) are the caller's, both must be met,
and the message says in as many words that whether it is a no is a judgement about this client
rather than something a report can make. Two chases in two days is a keen agent; two over three
weeks is a client who has booked elsewhere, and no default can tell them apart.

**Clients are not users** (Stage 7.2, 2026-09-04)
§7.1 gave an accepted quote somewhere to go. Stage 7.2 is the client's own view of it — the
itinerary, the statement, the document they agreed to — and the first decision is what a client
logs in with. The answer is **nothing**.

A `users` row carries roles and permissions into every guard in the system, and a client belongs
on the other side of that boundary; putting them in the same table means every future permission
check has to remember which kind of row it is looking at. And practically: somebody books one trip
every year or two, so a password is a thing they will have forgotten by the time they need it. The
login-support cost is real and what it protects is one itinerary. So access is a **grant**:
`booking_access_grants`, one high-entropy token per booking, which an agent sends by hand (nothing
sends anything — §5.3).

**The token is stored hashed and returned exactly once.** A table of live links is a table of
credentials, so the row holds SHA-256 of a 256-bit token and there is no endpoint that can give
the plaintext back. An agent who needs to resend issues a new grant — one click, and separately
revocable, which is what you actually want once the first one has been forwarded into a family
group chat. SHA-256 rather than bcrypt on purpose: a work factor exists to make guessing a
*low*-entropy secret expensive, there is nothing here to slow down, bcrypt would truncate at 72
bytes, and the value has to be looked up by equality. For the same reason there is no
brute-force lockout: 256 bits needs none, and a lockout on an unauthenticated endpoint keyed on a
token is a way for a stranger to lock a client out of their own itinerary.

**The token travels in the link's fragment** (`…/trip#<token>`), because a browser never sends a
fragment to a server: not in an access log, not in a Referer header when the client clicks through
from their itinerary to an airline's site. The portal app lifts it out and sends it as a bearer
token — the same transport as a staff login, so there is one convention and no token in a path or
a query string.

**A grant is scoped to one booking, not to a client**, so a leaked link exposes one trip rather
than a relationship. A booking can carry several (the person paying and the person travelling),
and revoking one leaves the others working. Revocation requires a reason and keeps it, for §5.3's
argument about a voided log entry: the next agent has to be able to tell a leak from a mistake.

**Read-only by construction.** There is no write endpoint on the portal at all, so there is
nothing a grant could be tricked into authorising — the guarantee is the absence of code rather
than a check somebody has to maintain. A grant is also not a login: it carries no user and no
permissions, and a test walks it at the staff endpoints to prove it opens none of them.

**The client view is an allow-list, not the snapshot minus cost.** This is the part worth reading
twice. `quote_versions.snapshot` holds the trip *and* the internal costing —
`cost_subtotal`, `profit_value`, `supplier_paid_total`, the per-component breakdown. A portal that
returned the snapshot with a few keys removed would be one forgotten key away from showing a
client the margin on their own holiday, and the key that gets forgotten is always the one added
later by somebody working on pricing who has never read the portal module. So nothing is removed:
named fields are copied across, and a field the snapshot gains tomorrow does not appear until
somebody adds it deliberately. There is a test that hands in a snapshot carrying invented extra
cost fields and asserts none of them come out. This is §2's internal/client split carried into §7
— a boundary that holds because of what the code *cannot* do.

**Everything comes from the frozen version, never a re-price.** Same principle as §7.1 booking
against the version: the trip, the itinerary and the document are the ones the client accepted,
and the document is pinned to the booking's own `quote_version_id` rather than the quote's current
one. A client who re-opens their proposal in March must see what they agreed to in September. The
money comes off the **booking**, where §7.1 froze it — reading it back off the snapshot here would
quietly undo that.

**Only the option they booked.** A quote offers three to nine (§3.7); showing a client the two
they turned down re-opens a decision they have already paid a deposit on. Where a booking has no
selected option it falls back to the recommended one, which is better than an empty page.

**The statement is the operator's arithmetic, not a second copy.** Straight through to §7.1's
`position`. Two implementations would eventually disagree, and the client's copy is the one
sitting in somebody's inbox.

**A dead link says which kind of dead it is.** Expired, withdrawn, or a cancelled booking — three
different sentences, because "this link no longer works" sends a client to the telephone with
nothing to say, and a client who has cancelled and is told their link expired will reasonably
conclude the system has lost their booking. A cancelled booking is reported as cancelled even
where the link has also lapsed. An *unknown* token gets the same wording as an expired one,
deliberately: anything else tells a stranger holding a guessed string whether they got close.

**A link outlives the trip.** Default expiry is departure plus a configured margin
(`PORTAL_ACCESS_DAYS_AFTER_TRAVEL`, 90 days), floored at 30 days from today so that a
late-recorded booking does not get a link that was dead on arrival. Not departure: the statement,
the receipts and the itinerary are all wanted afterwards, and a link that dies on the day they fly
home is a support call rather than a security measure.

**`last_seen_at`/`view_count`, not an access log.** "Did they even open the itinerary?" is a real
sales question and the cheapest sign a link has ended up somewhere it should not be. A row per
page view would grow without bound and prove nothing extra — a forwarded link is
indistinguishable from the client's own browser, so it is not evidence in a dispute either.

**`portal:manage` is its own permission.** Handing somebody outside the business a credential is
not the same act as editing a booking. There is no read/write pair because there is nothing to
read: the table holds hashes and a listing never shows a token.

**What is not built:** the portal *application*. `apps/portal` does not exist yet, and this stage
deliberately builds the API the boundary has to be enforced at rather than a page — business logic
lives in the backend, and the rule that no cost reaches a client is worth nothing if it lives in a
React component. When the app is built, the one piece of machinery worth adding is exchanging the
fragment token for a short-lived session so it is not held in JavaScript for the length of a
visit.

**A vehicle was a costing input, never a thing that could be busy** (Stage 8.1, 2026-09-05)
§2.5 put vehicles in the database so a drive could be charged: a Land Cruiser with a fuel
consumption and a daily operating cost, which §4.2 reads. Nothing anywhere said a particular
vehicle was *out*. Two bookings could be priced with the same one over the same week and the first
anybody would know is a Tuesday morning in Diani with one vehicle and two groups. §7.1 made it
worse rather than better: a confirmed booking now existed, and still nothing said who was driving.

Two tables close it. **`crew`** is the register of drivers and guides — one table, with `roles` as
a list, because in this market a driver-guide is usually one person. Two tables, or two rows,
would mean assigning the same human twice, double-booking them against themselves, and counting
them twice on a cost sheet. **`trip_assignments`** is one vehicle *or* one person committed to one
booking over a window, with a CHECK enforcing the exclusive-or: a row with neither commits nothing,
and a row with both would make "what is out on the 5th" a query with a branch in it.

**No `trips` table.** The booking already carries the dates, the headcount and the reference, and
a second row repeating them is a second thing to keep in step. A group of twelve in two Land
Cruisers with two driver-guides is four assignment rows and no special case.

**The window is stored, not derived from the booking.** A vehicle leaving Nairobi the night before
a coast pickup is out that night, and a fleet calendar that says otherwise will hand it to
somebody else on the Sunday.

**The overlap rule, which is the part worth reading twice.** A vehicle dropping a group at the
airport on the 5th and collecting another that afternoon is a normal Tuesday at a coast operator;
a vehicle on two trips over the 5th and 6th is a Tuesday that does not happen. So: any shared day
is a clash, **except** a single shared day that is one window's last and the other's first — and
that handover is still returned as an advisory, because a tight one and a comfortable one look
identical once the response says only "created".

The first version of that rule was wrong at the edges, and the tests name the case. Treating
"shares only a boundary day" as the handover makes two *single-day* trips on the 5th read as a
handover, which is two groups and one vehicle; and it gave different answers depending on which
window was asked. The rule that holds is: exactly one shared day, **and** neither window is that
day. Four edge tests pin it, including both directions of the single-day case.

**A clash is refused; an override is recorded.** The default is no, because the alternative is a
calendar that documents disasters rather than preventing them. But an operator who knows the first
booking is cancelling on Friday needs a way through, and the way through leaves `override_reason`
and `assigned_by` on the row. The point is not to make it impossible — it is to make it
attributable, which is the same shape as §5.3's amendment stamp.

**A cancelled booking releases what it held** — by exclusion rather than deletion: the clash query
only looks at active bookings. Otherwise the calendar fills with vehicles nobody is using, and an
operator told twice that a free vehicle is busy stops believing it. Crewing a cancelled booking is
refused outright, with that sentence as the reason.

**The licence is a date, not a valid/invalid flag.** The case worth catching is a licence expiring
*in the middle of a safari*: it passes every check made on the Monday and the group is in Tsavo on
the day it lapses. Only a date can catch that, and the refusal names the day. A licence expiring
shortly *after* a trip is a warning on the board rather than a refusal — it will not stop this
trip, it will stop the next one.

**Seats are counted across the booking, not per vehicle**, because twelve people in two Land
Cruisers is the normal answer and a per-vehicle check would refuse it. And they are not counted at
all until there is a vehicle: "no vehicle" already says it, and one problem should be reported
once.

**The role is on the assignment, not inferred from the person.** Somebody down as a driver-guide
can be sent out on one trip to drive and on another purely to guide. Reading it off the person
would count a guide as a driver and report a trip with nobody at the wheel as ready. Where a
person has more than one role and the caller does not say, the assignment is refused: a trip sheet
has to name one, and guessing is how it ends up saying something nobody meant.

**A missing guide is deliberately not reported.** Whether a trip needs one depends on whether the
client asked and paid for one, and a board that complained about every self-drive booking is a
board nobody opens — §5.2's lesson about closing leads on a timer, applied to departures.

**An assignment is deleted, not voided** — the one place this codebase does hard-delete, and the
distinction is deliberate: §5.3's log records things that *happened*, while an assignment is a
*plan*. Nobody needs the history of a vehicle pencilled in on Tuesday and swapped on Wednesday,
and keeping it would make "what is out on the 5th" a query that has to exclude ghosts.

**The `operations` role finally has permissions.** It has said "extended in Stage 8" since Stage 1
and carried only `user:read`. It now reads bookings, the fleet and the catalogue, manages crew and
assignments, and logs communications (§5.3) — and it takes no money: `booking:record_payment` is
finance's, and a test asserts operations is refused at the till.
