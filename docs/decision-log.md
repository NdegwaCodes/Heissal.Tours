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
