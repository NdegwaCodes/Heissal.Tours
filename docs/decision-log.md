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
