"""The client-facing view model for a quotation document.

This module is the internal/client boundary made structural (§2). It is the only
thing the template is given, and it has **no field for cost, margin, supplier
payments, contingency, profit or the agent cover fee**. A template cannot print
what it was never handed, so the boundary holds however the markup changes later.

Two sources feed it, deliberately:

* **Money and option detail come from the version snapshot.** An issued document
  must render the same figures in a year's time, so nothing priced is looked up
  live. Property renames and rate changes cannot reach an issued quotation.
* **Imagery comes from the live tables.** Photographs are presentation, not
  terms: replacing a dark photo of a hotel does not change what was quoted, and
  freezing image ids into a snapshot would leave an old document unable to show a
  picture that was merely re-cropped.

Images are **inlined as data URIs**, not linked. The document has to be
self-contained: the PDF renderer is given no network access and no credentials,
and even the HTML is served to a caller holding a bearer token that a browser
would not replay when fetching an ``<img>``. Linking produced a proposal with a
row of broken images in every context that mattered. ``inline_assets=False``
keeps the URLs for an admin preview that can authenticate its own fetches.

Sections whose data the quote does not carry are simply absent — the transport
page needs transport segments, the signature-experience page needs an activity
flagged for its own section. An empty section is omitted rather than filled with
plausible copy: a proposal that describes transfers the client is not getting is
worse than one that stays quiet about them.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import read_bytes
from app.integrations.narrative import ACCOMMODATION
from app.modules.accommodations.models import Accommodation
from app.modules.activities.models import Activity, ActivityPriceTier
from app.modules.activities.service import ActivityRateService
from app.modules.clients.models import Client
from app.modules.destinations.models import Destination
from app.modules.documents.config import DocumentConfig
from app.modules.media.models import DestinationImage, PropertyImage
from app.modules.narratives.service import NarrativeService
from app.modules.quotes.models import Quote, QuoteVersion

# How a meal-plan code reads on a document. Presentation of a stored code, not a
# business rule: the plan's own name is used when the catalogue has one, and this
# is the fallback for codes an admin never named.
MEAL_PLAN_PHRASES: dict[str, str] = {
    "AI": "All-inclusive meals",
    "FB": "Full-board meals",
    "HB": "Half-board meals",
    "BB": "Bed & breakfast",
    "RO": "Room only",
}

# Plans where the group arranges its own catering, so the document says so
# instead of implying the hotel feeds them (§3.4).
SELF_CATERING_PLANS = frozenset({"BB", "RO"})


def money(amount: Decimal | str | None, currency: str) -> str | None:
    """``KES 1,065,000`` — grouped, and without the cents that never differ.

    Per-person figures are rounded to a whole hundred (§3.6), so a trailing
    ``.00`` on every price on the document would be noise. A fractional amount
    still prints its decimals rather than being silently truncated.
    """
    if amount is None:
        return None
    value = Decimal(amount)
    if value == value.to_integral_value():
        return f"{currency} {int(value):,}"
    return f"{currency} {value:,.2f}"


@dataclass(frozen=True)
class Fact:
    """One label/value cell in a fact strip."""

    label: str
    value: str


@dataclass(frozen=True)
class Image:
    url: str
    alt: str


@dataclass(frozen=True)
class ItineraryLeg:
    """One stay within a package, as it reads on the document (§3.9, §3.11)."""

    step: str
    destination: str
    property_name: str
    nights: str
    room: str
    board: str


@dataclass(frozen=True)
class DayView:
    """One day of the trip, as it reads on the proposal (Stage 4.1).

    Every field is already a sentence fragment: the template's job is layout,
    and a template deciding whether to write "Breakfast, then checkout" is a
    template holding commercial wording.
    """

    label: str
    on: str
    place: str
    property_name: str
    #: "Coral Sands → Baobab Beach Lodge" on a day the package moves hotels,
    #: empty otherwise.
    move: str
    #: The movements and excursions of the day, in the order they happen.
    events: list[str]
    board: str
    is_arrival: bool = False
    is_departure: bool = False


@dataclass(frozen=True)
class CohortPriceView:
    """What one group of travellers pays, in their own currency (§3.8).

    The reason this exists on the document at all: a group of residents and
    non-residents has no single per-person figure — they are charged different
    rates in different currencies — so ``per_person`` above is deliberately
    NULL for them, and without these rows such a client sees a group total and
    nothing else.
    """

    label: str
    headcount: str
    per_person: str
    total: str


@dataclass
class OptionView:
    """One accommodation option, as the client sees it."""

    number: str
    name: str
    tagline: str
    blurb: str | None
    is_recommended: bool
    is_comparable: bool
    facts: list[Fact]
    included: list[str]
    per_person: str | None
    group_total: str
    hero: Image | None
    gallery: list[Image] = field(default_factory=list)
    # Printed as an italic aside where the option is not directly comparable
    # with the others — the reference proposal does exactly this for its villas.
    comparability_note: str | None = None
    # The legs of a curated package, in itinerary order. A single-property
    # option has one, and the template prints the table only past that: the
    # facts panel already says everything a one-hotel option has to say.
    itinerary: list[ItineraryLeg] = field(default_factory=list)
    # "Diani → Maasai Mara", for the comparison table and the option heading.
    route: str = ""
    # Per-cohort prices where the group is not uniform (§3.8).
    cohorts: list[CohortPriceView] = field(default_factory=list)
    # The day-by-day programme (Stage 4.1) — what happens on which day, which
    # is what a client reads before they look at the price.
    days: list[DayView] = field(default_factory=list)


@dataclass(frozen=True)
class ComparisonRow:
    name: str
    route: str
    rooming: str
    meal_plan: str
    transport: str
    per_person: str | None
    group_total: str
    is_recommended: bool


@dataclass(frozen=True)
class RejectedView:
    name: str
    reason: str


@dataclass(frozen=True)
class TransferLeg:
    route: str
    note: str


@dataclass
class TransportView:
    heading: str
    summary: str
    route_label: str
    legs: list[TransferLeg]
    capacity: str
    hero: Image | None = None
    # How the journey reads in one cell of the comparison table.
    label: str = ""
    # Flights: named, never priced, because Heissal holds no ticketing licence
    # (§3.10). Printed as something for the client to book, not as a charge.
    named: list[str] = field(default_factory=list)
    # What the optional upgrades sell for, if any were quoted.
    add_on: str | None = None


@dataclass
class ExperienceView:
    """An activity important enough for its own page (§3.9, §3.11)."""

    name: str
    description: str | None
    includes: list[str]
    per_person: str | None
    is_optional: bool
    tiers: list[Fact] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)


@dataclass
class QuotationView:
    config: DocumentConfig
    quote_number: str
    title: str
    subtitle: str
    destination_label: str
    client_name: str
    currency: str
    cover: Image | None
    cover_facts: list[Fact]
    intro_heading: str
    intro_paragraphs: list[str]
    glance: list[Fact]
    options: list[OptionView]
    comparison: list[ComparisonRow]
    rejected: list[RejectedView]
    transport: TransportView | None
    experiences: list[ExperienceView]
    # What the quoted price does not cover (§3.12). The standing list from
    # config, plus whatever this quote makes true — the flights we cannot
    # ticket, the upgrades quoted separately.
    exclusions: list[str]
    # The day-by-day programme (Stage 4.1), for ONE option — see
    # ``QuotationViewBuilder._programme`` for which and why.
    programme: list[DayView]
    programme_option: str
    valid_until: date | None
    issued_on: date


class QuotationViewBuilder:
    """Assembles the view model for one issued version."""

    def __init__(self, db: AsyncSession, *, inline_assets: bool = True):
        self.db = db
        self.inline_assets = inline_assets

    async def build(
        self, quote: Quote, version: QuoteVersion, config: DocumentConfig
    ) -> QuotationView:
        snapshot = version.snapshot or {}
        currency = version.currency.upper()
        pax = int(snapshot.get("pax_count") or quote.pax_count or 0)
        nights = self._nights(quote)
        client = await self.db.get(Client, quote.client_id)
        destination = await self._destination(quote)
        # From the snapshot, not from the live segments: an issued document has
        # to keep describing the journey it was issued with, and segments can be
        # edited afterwards. A version frozen before §3.11 has no journey in it
        # and gets no transport page, which is the honest outcome — better than
        # a page of what the quote looks like today.
        transport = await self._transport(
            snapshot.get("transport") or {}, pax, destination, currency
        )

        raw_options = sorted(
            snapshot.get("options", []), key=lambda o: o.get("sort_order") or 0
        )
        options: list[OptionView] = []
        for index, raw in enumerate(raw_options, start=1):
            options.append(
                await self._option(
                    raw, index, currency, pax, transport is not None, config
                )
            )

        programme, programme_option = self._programme(options)

        return QuotationView(
            config=config,
            quote_number=quote.quote_number,
            title=quote.document_title or self._default_title(destination),
            subtitle=(
                quote.document_subtitle
                or f"A curated experience by {config.company_name}"
            ),
            destination_label=self._destination_label(destination),
            client_name=client.name if client else "",
            currency=currency,
            cover=await self._cover(destination),
            cover_facts=self._cover_facts(
                pax, destination, client, options, transport
            ),
            intro_heading=self._intro_heading(destination),
            intro_paragraphs=self._intro(config, pax, len(options), destination),
            glance=self._glance(pax, nights, destination, options, transport, config),
            options=options,
            comparison=self._comparison(options, transport),
            rejected=[
                RejectedView(name=r["name"], reason=r["reason"])
                for r in snapshot.get("rejected", [])
            ],
            transport=transport,
            experiences=await self._experiences(quote, currency),
            exclusions=self._exclusions(config, transport),
            programme=programme,
            programme_option=programme_option,
            valid_until=quote.valid_until,
            issued_on=version.created_at.date(),
        )

    # -- options ------------------------------------------------------------- #

    async def _option(
        self,
        raw: dict,
        index: int,
        currency: str,
        pax: int,
        has_transport: bool,
        config: DocumentConfig,
    ) -> OptionView:
        plan_code = str(raw.get("meal_plan_code") or "").upper()
        plan_name = str(raw.get("meal_plan_name") or plan_code)
        room = str(raw.get("room_type_name") or "")
        rooms = raw.get("rooms_required")
        nights = raw.get("nights")
        accommodation_id = raw.get("accommodation_id")

        facts = [
            Fact("Accommodation", room),
            Fact("Meal plan", plan_name),
            Fact("Group size", f"{pax} participants"),
        ]
        if rooms:
            count = int(rooms)
            facts.append(
                Fact("Rooms required", f"{count} room" + ("s" if count > 1 else ""))
            )
        if nights:
            facts.append(Fact("Nights", f"{nights} nights"))
        if has_transport:
            facts.append(Fact("Transport", "Complete group transfers"))

        included = ["Accommodation"]
        if plan_code in SELF_CATERING_PLANS:
            # The hotel is not feeding them, so the document says what actually
            # happens rather than implying board it does not include (§3.4).
            included.append("Group meal arrangement")
        else:
            included.append(MEAL_PLAN_PHRASES.get(plan_code, plan_name))
        if has_transport:
            included.append("Complete group transfers")
        # The mandatory activities, by name. They are charged into the price,
        # so naming them here is not marketing: it is what the client is
        # paying for, said in the words they will recognise.
        included.extend(
            str(name) for name in (raw.get("activities") or []) if str(name)
        )
        included.append(f"{config.company_name} coordination")

        legs = sorted(
            raw.get("legs") or [], key=lambda leg: leg.get("sequence") or 0
        )
        itinerary = [
            ItineraryLeg(
                step=f"{index:02d}",
                destination=str(leg.get("destination_name") or ""),
                property_name=str(leg.get("accommodation_name") or ""),
                nights=self._nights_phrase(leg.get("nights")),
                room=str(leg.get("room_type_name") or ""),
                # The plan for this leg, which for a package is a choice and
                # not a compromise: a day out of the hotel makes half board the
                # right board (§3.9). The fallback reason stays internal.
                board=str(leg.get("meal_plan_name") or leg.get("meal_plan_code") or ""),
            )
            for index, leg in enumerate(legs, start=1)
        ]
        route = " → ".join(
            one.destination or one.property_name for one in itinerary
        )
        # No "Itinerary" fact cell: the heading already names the properties and
        # the table below lists the legs, so a third copy of the same route cost
        # three lines of a page that has to fit A4.

        cohorts = [
            CohortPriceView(
                label=self._cohort_label(row),
                headcount=self._people_phrase(row.get("headcount")),
                per_person=money(row.get("per_person"), str(row.get("currency") or ""))
                or "",
                total=money(row.get("total"), str(row.get("currency") or "")) or "",
            )
            for row in raw.get("cohorts") or []
        ]
        if len(cohorts) < 2:
            # One cohort means a uniform group, and the panel would repeat the
            # per-person figure printed in large type immediately above it.
            # These rows exist for the group that has no single figure.
            cohorts = []

        days = self._days(raw.get("days") or [])

        hero, gallery = await self._property_images(accommodation_id)
        # Frozen with the version since §4.4; the live lookup is the fallback
        # for versions issued before it.
        blurb = raw.get("blurb") or await self._blurb(accommodation_id)
        comparable = bool(raw.get("is_comparable", True))
        note = None
        if not comparable:
            note = (
                f"This option is structured differently from the others — "
                f"{plan_name.lower()} rather than a like-for-like package — so it "
                f"is not presented as directly equivalent."
            )

        return OptionView(
            number=f"{index:02d}",
            name=str(raw.get("accommodation_name") or ""),
            tagline=" · ".join(part for part in (plan_name, room) if part),
            blurb=blurb,
            is_recommended=bool(raw.get("is_recommended")),
            is_comparable=comparable,
            facts=facts,
            included=included,
            per_person=money(raw.get("per_person"), currency),
            # The figure the cohort rows below it add up to (§3.6). Older
            # versions froze only the build-up's own total, so that is the
            # fallback rather than a blank.
            group_total=money(
                raw.get("client_total") or raw.get("group_total"), currency
            )
            or "",
            hero=hero,
            gallery=gallery,
            comparability_note=note,
            itinerary=itinerary,
            days=days,
            route=route,
            cohorts=cohorts,
        )

    @staticmethod
    def _programme(options: list[OptionView]) -> tuple[list[DayView], str]:
        """The one option whose day-by-day is printed, and its name (Stage 4.1).

        **One page, not one per option.** Every option carries its own frozen
        programme, because which day a client is in the Mara depends on the
        package — but five options is five near-identical pages of the same
        journey, and a proposal that repeats itself is one nobody finishes.
        The recommended option is the one the document leads on everywhere
        else, so it is the one whose days are printed; where nothing is
        recommended the first option stands in.

        **Printed only where there is something to say.** A four-day beach stay
        with no movements and no excursions would produce "Diani, full board"
        four times over, which is padding — and this document's whole argument
        is that it does not pad. So the page appears when the trip actually has
        a shape: a journey, an excursion, or more than one property.
        """
        if not options:
            return [], ""
        chosen = next((one for one in options if one.is_recommended), options[0])
        days = chosen.days
        worth_printing = any(day.events for day in days) or len(chosen.itinerary) > 1
        if not days or not worth_printing:
            return [], ""
        return days, chosen.name

    def _days(self, frozen: list[dict]) -> list[DayView]:
        """The frozen programme as the proposal reads it (Stage 4.1).

        Read from the snapshot and never recomputed, like every other figure on
        this page: a leg re-dated after the quote went out must not change the
        document the client is looking at.

        The wording lives here rather than in the pure layer because it is
        commercial language. Two choices worth naming:

        * **The departure day says checkout and nothing about meals.** It has
          no night under it, so it has no board of its own; printing the last
          leg's basis there would promise a lunch and a dinner nobody bought,
          and promising a breakfast instead is only right until a room-only
          leg makes it wrong.
        * **Nothing is invented for a quiet day.** A day with no movement and
          no excursion says where they are and what board they are on, and
          stops. Filling it with "day at leisure" is the sort of copy that
          reads as padding, and a proposal that pads is one a client stops
          trusting on the figures too.
        """
        out: list[DayView] = []
        for row in frozen:
            events = [
                self._movement_phrase(one) for one in (row.get("movements") or []) if one
            ]
            events.extend(str(one) for one in (row.get("excursions") or []) if one)
            board = str(row.get("board") or "")
            phrase = (
                "Checkout"
                if row.get("is_departure")
                else MEAL_PLAN_PHRASES.get(board, board)
            )
            place = str(row.get("destination") or "")
            property_name = str(row.get("property_name") or "")
            leaving = str(row.get("moves_from") or "")
            out.append(
                DayView(
                    label=f"Day {int(row.get('number') or 0):02d}",
                    on=self._day_date(str(row.get("date") or "")),
                    place=place,
                    property_name=property_name,
                    move=(
                        f"{leaving} \u2192 {property_name}"
                        if leaving and property_name
                        else ""
                    ),
                    events=events,
                    board=phrase,
                    is_arrival=bool(row.get("is_arrival")),
                    is_departure=bool(row.get("is_departure")),
                )
            )
        return out

    @classmethod
    def _movement_phrase(cls, movement: Any) -> str:
        """"Diani to the Mara — about 4 h 30", where the route table knows.

        Versions frozen before §4.2 hold a plain label and get one, which is
        the whole reason this reads both shapes: an issued document must keep
        rendering as it was issued.
        """
        if not isinstance(movement, dict):
            return str(movement)
        label = str(movement.get("label") or "")
        phrase = cls._drive_phrase(movement.get("minutes"))
        return f"{label} — {phrase}" if label and phrase else label

    @staticmethod
    def _drive_phrase(minutes: Any) -> str:
        """"about 45 min", "about 4 h 30" — hedged, because a road is a road.

        Always "about". The figure is the operator's own timing of a Kenyan
        road, and printing it flat invites a client to hold a proposal to a
        four-hour-thirty-two arrival on a route where a lorry on the escarpment
        costs an hour.
        """
        try:
            total = int(minutes)
        except (TypeError, ValueError):
            return ""
        if total <= 0:
            return ""
        if total < 90:
            return f"about {total} min"
        hours, rest = divmod(total, 60)
        return f"about {hours} h" if not rest else f"about {hours} h {rest:02d}"

    @staticmethod
    def _day_date(iso: str) -> str:
        """"Wed 01 Jul" — the weekday matters on an itinerary.

        A client checking a programme against their own diary is checking days
        of the week as much as dates, and the year is already on the cover.
        """
        if not iso:
            return ""
        try:
            when = date.fromisoformat(iso)
        except ValueError:
            return iso
        return when.strftime("%a %d %b")

    @staticmethod
    def _nights_phrase(nights: Any) -> str:
        count = int(nights or 0)
        return "" if not count else f"{count} night" + ("s" if count > 1 else "")

    @staticmethod
    def _people_phrase(headcount: Any) -> str:
        count = int(headcount or 0)
        return "" if not count else f"{count} traveller" + ("s" if count > 1 else "")

    @staticmethod
    def _cohort_label(row: dict) -> str:
        """"Kenyan citizens · children" — the label, never our own key.

        The residence *name* is frozen into the snapshot beside the figures for
        exactly this: printing ``non_resident`` on a proposal leaks how we
        store things into what a client reads.
        """
        residence = str(row.get("residence_label") or row.get("residence") or "")
        kind = str(row.get("traveller_type") or "").strip()
        if kind and kind != "adult":
            return f"{residence} · {kind}ren" if kind == "child" else (
                f"{residence} · {kind}s"
            )
        return residence

    def _comparison(
        self, options: list[OptionView], transport: TransportView | None
    ) -> list[ComparisonRow]:
        """The curated package × transport table, cheapest first (§3.11).

        Sorted by price rather than by the option order used for the individual
        pages, which is what the reference proposal does: the pages lead with the
        recommendation, the table lets a client scan on cost.

        The route column is what makes this a *package* comparison rather than a
        hotel one. Two packages can share their first property and differ two
        legs later, so a table keyed on the property name alone would show a
        client two rows they cannot tell apart.

        Transport reads the same in every row on purpose — it is the same
        journey whichever package is chosen — and saying so once per row is
        what stops a client reading the cheapest bed as the cheapest trip.
        """
        rows = [
            ComparisonRow(
                name=option.name,
                route=option.route,
                rooming=next(
                    (f.value for f in option.facts if f.label == "Accommodation"), ""
                ),
                meal_plan=next(
                    (f.value for f in option.facts if f.label == "Meal plan"), ""
                ),
                transport=(
                    transport.label or transport.heading
                    if transport is not None
                    else "Not included"
                ),
                per_person=option.per_person,
                group_total=option.group_total,
                is_recommended=option.is_recommended,
            )
            for option in options
        ]
        return sorted(rows, key=lambda r: _numeric(r.group_total))

    # -- headings and intro copy --------------------------------------------- #

    @staticmethod
    def _default_title(destination: Destination | None) -> str:
        if destination is None:
            return "Travel Proposal"
        return f"{destination.name} Experience"

    @staticmethod
    def _destination_label(destination: Destination | None) -> str:
        if destination is None:
            return ""
        parts = [destination.region, destination.name]
        return " · ".join(p for p in parts if p)

    @staticmethod
    def _intro_heading(destination: Destination | None) -> str:
        where = destination.name if destination else "Your Trip"
        return f"A {where} Experience, Curated for Your Team"

    @staticmethod
    def _intro(
        config: DocumentConfig,
        pax: int,
        option_count: int,
        destination: Destination | None,
    ) -> list[str]:
        where = destination.name if destination else "your chosen destination"
        return [
            f"{config.company_name} is delighted to present this curated proposal "
            f"for a group of {pax} participants.",
            f"We have sourced {option_count} accommodation options across different "
            f"levels of comfort and experience, allowing your organisation to select "
            f"the one that best aligns with its preferred balance of hospitality, "
            f"convenience and budget.",
            f"From coordinated group transportation to accommodation and meals, our "
            f"role is to make the journey seamless from departure to arrival, "
            f"allowing your team to focus on connection, relaxation and enjoying "
            f"{where}.",
        ]

    def _cover_facts(
        self,
        pax: int,
        destination: Destination | None,
        client: Client | None,
        options: list[OptionView],
        transport: TransportView | None,
    ) -> list[Fact]:
        facts = [Fact("Group", f"{pax} participants")]
        if destination is not None:
            facts.append(Fact("Destination", destination.name))
        if client is not None:
            facts.append(Fact("Client", client.name))
        if options:
            facts.append(Fact("Options", f"{len(options)} to choose from"))
        if transport is not None:
            facts.append(Fact("Transfers", transport.capacity))
        return facts

    def _glance(
        self,
        pax: int,
        nights: int,
        destination: Destination | None,
        options: list[OptionView],
        transport: TransportView | None,
        config: DocumentConfig,
    ) -> list[Fact]:
        facts = [Fact("Group size", f"{pax} participants")]
        if destination is not None:
            facts.append(Fact("Destination", self._destination_label(destination)))
        if transport is not None:
            facts.append(Fact("Transport", transport.heading))
        if nights:
            facts.append(Fact("Duration", f"{nights} nights"))
        if options:
            facts.append(Fact("Accommodation", f"{len(options)} curated options"))
        facts.append(Fact("Pricing", config.vat_note))
        return facts

    @staticmethod
    def _exclusions(
        config: DocumentConfig, transport: TransportView | None
    ) -> list[str]:
        """What the price does not cover, standing list first (§3.12).

        The quote-specific lines are the ones worth getting right. A flight we
        cannot ticket has to appear here as well as on the transport page —
        this is the list a client checks before they sign — and an optional
        upgrade has to be named as outside the package, or the total on the
        comparison table reads as covering it.
        """
        out = list(config.exclusions)
        if transport is not None:
            for flight in transport.named:
                out.insert(
                    0,
                    f"Air tickets ({flight}) — Heissal does not ticket air "
                    f"travel, so these are booked directly by you",
                )
            if transport.add_on:
                out.append(
                    f"Optional transport upgrades, quoted separately at "
                    f"{transport.add_on}"
                )
        return out

    @staticmethod
    def _nights(quote: Quote) -> int:
        return max(0, (quote.departure_date - quote.arrival_date).days)

    # -- transport ----------------------------------------------------------- #

    async def _transport(
        self,
        journey: dict,
        pax: int,
        destination: Destination | None,
        currency: str,
    ) -> TransportView | None:
        """Describe the journey the quote was issued with (§3.10, §3.11).

        Described and never priced per leg: the movements' fares are what we
        pay, and their total is already inside every option's figure. What the
        client needs from this page is what is being arranged for them, which
        of it is an optional upgrade and what it costs, and which tickets are
        theirs to buy.

        A quote with no movements gets no transport page rather than a page of
        assumptions — a proposal describing transfers the client is not getting
        is worse than one that stays quiet.
        """
        movements = sorted(
            journey.get("movements") or [], key=lambda m: m.get("sequence") or 0
        )
        named = [str(one) for one in journey.get("named") or []]
        if not movements and not named:
            return None

        # Counted, not listed one by one. A return rail journey with its four
        # mandatory transfers is six movements and two distinct routes, and a
        # page that prints "Terminus to hotel — Included" four times reads as a
        # bug rather than as thoroughness.
        counted: dict[tuple[str, str], int] = {}
        for one in movements:
            key = (
                str(one.get("label") or "").strip() or self._movement_label(one),
                "Optional upgrade" if one.get("is_optional") else "Included",
            )
            counted[key] = counted.get(key, 0) + 1
        legs = [
            TransferLeg(
                route=route if count == 1 else f"{route} × {count}", note=note
            )
            for (route, note), count in counted.items()
        ]
        # Flights sit in the same list, marked as the client's to book: leaving
        # them off the page entirely is how a client turns up without a ticket.
        legs.extend(TransferLeg(route=one, note="Booked by you") for one in named)
        modes = sorted({str(one.get("mode") or "").upper() for one in movements})
        add_on = Decimal(str(journey.get("optional_price") or "0"))
        return TransportView(
            heading=" + ".join(modes) if modes else "Air",
            summary=(
                "To keep the group travelling together comfortably, transport is "
                "arranged as a complete door-to-door journey, and the same in "
                "reverse."
            ),
            # The distinct routes, in order — the same de-duplication as above,
            # for the same reason.
            route_label=" → ".join(dict.fromkeys(leg.route for leg in legs)),
            legs=legs,
            capacity=f"{pax} pax",
            hero=await self._cover(destination) if destination else None,
            label=self._journey_label(modes, named),
            named=named,
            add_on=money(add_on, currency) if add_on > 0 else None,
        )

    @staticmethod
    def _movement_label(movement: dict) -> str:
        kind = "Transfer" if movement.get("kind") == "transfer" else "Line haul"
        return f"{kind} · {str(movement.get('mode') or '').upper()}"

    @staticmethod
    def _journey_label(modes: list[str], named: list[str]) -> str:
        """The journey in one cell of the comparison table."""
        words = [mode.title() for mode in modes]
        if not words:
            return "Flights booked by you"
        joined = words[0] if len(words) == 1 else " and ".join(
            (", ".join(words[:-1]), words[-1])
        )
        return f"{joined} transfers" + (" · flights excluded" if named else "")

    # -- signature experiences ------------------------------------------------ #

    async def _experiences(
        self, quote: Quote, currency: str
    ) -> list[ExperienceView]:
        """Activities the agent flagged for a page of their own (§3.11)."""
        activity_ids = [
            selection.activity_id
            for leg in quote.legs
            for selection in leg.activities
        ]
        if not activity_ids:
            return []
        activities = (
            (
                await self.db.execute(
                    select(Activity).where(
                        Activity.id.in_(activity_ids),
                        Activity.has_own_section.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        rates = ActivityRateService(self.db)
        out: list[ExperienceView] = []
        for activity in activities:
            per_person = None
            try:
                rate = await rates.select_rate(
                    activity_id=activity.id,
                    residence_category_id=quote.residence_category_id,
                    on_date=quote.arrival_date,
                )
                per_person = money(rate.adult_price, rate.currency.upper())
            except Exception:  # noqa: BLE001
                # No rate for this category and date. The experience is still
                # worth describing; the price is left off rather than guessed.
                per_person = None
            tiers = (
                (
                    await self.db.execute(
                        select(ActivityPriceTier)
                        .where(
                            ActivityPriceTier.activity_id == activity.id,
                            ActivityPriceTier.residence_category_id
                            == quote.residence_category_id,
                            ActivityPriceTier.effective_from <= quote.arrival_date,
                        )
                        .order_by(ActivityPriceTier.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            out.append(
                ExperienceView(
                    name=activity.name,
                    description=activity.description,
                    includes=[],
                    per_person=per_person,
                    is_optional=activity.is_optional,
                    tiers=[
                        Fact(tier.label, money(tier.price, tier.currency.upper()) or "")
                        for tier in tiers
                    ],
                    images=[],
                )
            )
        return out

    # -- imagery and blurbs --------------------------------------------------- #

    async def _cover(self, destination: Destination | None) -> Image | None:
        if destination is None:
            return None
        row = (
            await self.db.execute(
                select(DestinationImage)
                .where(DestinationImage.destination_id == destination.id)
                .order_by(
                    DestinationImage.is_cover.desc(), DestinationImage.sort_order
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._image(row, "destination-images", row.alt_text or destination.name)

    async def _property_images(
        self, accommodation_id: str | None
    ) -> tuple[Image | None, list[Image]]:
        if not accommodation_id:
            return None, []
        rows = (
            (
                await self.db.execute(
                    select(PropertyImage)
                    .where(
                        PropertyImage.accommodation_id == uuid.UUID(accommodation_id)
                    )
                    .order_by(
                        PropertyImage.is_hero.desc(), PropertyImage.sort_order
                    )
                )
            )
            .scalars()
            .all()
        )
        images = [
            image
            for image in (
                self._image(row, "property-images", row.alt_text or "") for row in rows
            )
            if image is not None
        ]
        if not images:
            return None, []
        # One hero and up to four thumbnails, which is the reference layout.
        return images[0], images[1:5]

    def _image(self, row: Any, kind: str, alt: str) -> Image | None:
        """A reference the document can actually resolve.

        A row whose bytes have gone missing returns ``None`` and the image is
        left out entirely. A broken image on a client proposal is worse than one
        fewer photograph, and it is not a reason to fail the whole render.
        """
        if not self.inline_assets:
            return Image(url=f"/api/v1/{kind}/{row.id}/file", alt=alt)
        try:
            payload = base64.b64encode(read_bytes(row.storage_path)).decode("ascii")
        except (OSError, ValueError):
            return None
        return Image(url=f"data:{row.content_type};base64,{payload}", alt=alt)

    async def _blurb(self, accommodation_id: str | None) -> str | None:
        """The paragraph under a property's photograph, resolved live.

        The fallback path. Since §4.4 the text is frozen into the version at
        issue — approving a replacement description must not rewrite a proposal
        already in a client's inbox — and this answers for the versions issued
        before that, which have no ``blurb`` in their snapshot.

        Precedence, and the first entry is §4.4: **approved** copy wins. It is
        the newest editorial decision about this property and somebody other
        than its author signed it off, which is more than the older columns can
        say. A draft is never reachable from here — the document layer asks
        only "is there approved copy", and that is the whole approval gate.

        Then the hand-written ``blurb``, then the catalogue ``description``,
        then nothing. Nothing is a perfectly good outcome: the option page
        reads fine without a paragraph, and filler under a photograph is worse
        than white space.
        """
        if not accommodation_id:
            return None
        subject_id = uuid.UUID(accommodation_id)
        approved = await NarrativeService(self.db).printable(
            ACCOMMODATION, subject_id
        )
        if approved is not None:
            return approved.text
        accommodation = await self.db.get(Accommodation, subject_id)
        if accommodation is None:
            return None
        return accommodation.blurb or accommodation.description

    async def _destination(self, quote: Quote) -> Destination | None:
        """The destination the proposal is about.

        Taken from the first leg where the quote has one, and otherwise from the
        properties being offered — an options-only quote has no legs, and every
        option on it hangs off the same place by construction (§3.10).
        """
        for leg in sorted(quote.legs, key=lambda leg: leg.sequence):
            found = await self.db.get(Destination, leg.destination_id)
            if found is not None:
                return found
        for option in sorted(quote.options, key=lambda o: o.sort_order):
            accommodation = await self.db.get(Accommodation, option.accommodation_id)
            if accommodation is not None:
                return await self.db.get(Destination, accommodation.destination_id)
        return None


def _numeric(formatted: str) -> Decimal:
    """The number inside ``KES 1,065,000``, for sorting."""
    digits = "".join(c for c in formatted if c.isdigit() or c == ".")
    return Decimal(digits) if digits else Decimal(0)
