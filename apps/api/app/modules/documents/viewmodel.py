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
from app.modules.accommodations.models import Accommodation
from app.modules.activities.models import Activity, ActivityPriceTier
from app.modules.activities.service import ActivityRateService
from app.modules.clients.models import Client
from app.modules.destinations.models import Destination
from app.modules.documents.config import DocumentConfig
from app.modules.media.models import DestinationImage, PropertyImage
from app.modules.quotes.models import Quote, QuoteTransportSegment, QuoteVersion

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


@dataclass(frozen=True)
class ComparisonRow:
    name: str
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
        transport = await self._transport(quote, pax, destination)

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
            comparison=self._comparison(options, transport is not None),
            rejected=[
                RejectedView(name=r["name"], reason=r["reason"])
                for r in snapshot.get("rejected", [])
            ],
            transport=transport,
            experiences=await self._experiences(quote, currency),
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
            facts.append(Fact("Rooms required", f"{rooms} rooms"))
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
        included.append(f"{config.company_name} coordination")

        hero, gallery = await self._property_images(accommodation_id)
        blurb = await self._blurb(accommodation_id)
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
            group_total=money(raw.get("group_total"), currency) or "",
            hero=hero,
            gallery=gallery,
            comparability_note=note,
        )

    def _comparison(
        self, options: list[OptionView], has_transport: bool
    ) -> list[ComparisonRow]:
        """The at-a-glance table, cheapest first.

        Sorted by price rather than by the option order used for the individual
        pages, which is what the reference proposal does: the pages lead with the
        recommendation, the table lets a client scan on cost.
        """
        rows = [
            ComparisonRow(
                name=option.name,
                rooming=next(
                    (f.value for f in option.facts if f.label == "Accommodation"), ""
                ),
                meal_plan=next(
                    (f.value for f in option.facts if f.label == "Meal plan"), ""
                ),
                transport="Group transfers" if has_transport else "Not included",
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
    def _nights(quote: Quote) -> int:
        return max(0, (quote.departure_date - quote.arrival_date).days)

    # -- transport ----------------------------------------------------------- #

    async def _transport(
        self, quote: Quote, pax: int, destination: Destination | None
    ) -> TransportView | None:
        """Describe the transport legs the quote actually carries.

        Only described, never priced here: the reference proposal shows no
        per-leg figure, and the transfer tariffs are Stage 3.8. A quote with no
        segments gets no transport page rather than a page of assumptions.
        """
        segments = sorted(quote.transport_segments, key=lambda s: s.sequence)
        if not segments:
            return None
        legs = [
            TransferLeg(
                route=segment.description or self._leg_label(segment),
                note=("Optional extra" if segment.is_optional else "Included"),
            )
            for segment in segments
        ]
        modes = sorted({s.mode.upper() for s in segments})
        return TransportView(
            heading=" + ".join(modes),
            summary=(
                "To keep the group travelling together comfortably, transport is "
                "arranged as a complete door-to-door journey, and the same in "
                "reverse."
            ),
            route_label=" → ".join(self._leg_label(s) for s in segments),
            legs=legs,
            capacity=f"{pax} pax",
            hero=await self._cover(destination) if destination else None,
        )

    @staticmethod
    def _leg_label(segment: QuoteTransportSegment) -> str:
        kind = "Transfer" if segment.kind == "transfer" else "Line haul"
        parts = [kind, segment.mode.upper()]
        if segment.travel_class:
            parts.append(segment.travel_class.title())
        return " · ".join(parts)

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
        if not accommodation_id:
            return None
        accommodation = await self.db.get(
            Accommodation, uuid.UUID(accommodation_id)
        )
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
