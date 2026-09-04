"""OptionPricingService — the lookups behind a Stage 3 multi-option quotation.

:mod:`app.modules.quotes.options` holds the arithmetic as pure functions. This
module is the half that talks to the database: it finds the rates, picks the room
type, resolves the meal plan, gathers the mandatory supplements, converts
currencies, and hands the components to
:func:`~app.modules.quotes.options.build_up`.

Three rules shape how it reads:

* **Rates are selected per night**, not once per stay. Season windows do not line
  up with itineraries, and a booking that crosses into the festive window would
  otherwise be priced entirely at the cheaper season (§3.1).
* **Cheapest within the hotel, never between hotels** (§3.7). Every option the
  agent put on the quote is priced; the client chooses between them.
* **A missing rate is not a client-facing rejection.** Only a rule the client can
  be shown — a minimum stay the itinerary does not meet — becomes a
  ``QuoteRejectedCandidate``. Anything else (no rate loaded for these dates, a
  room type nobody priced) is an internal warning, because the rejection reason
  is printed on the document verbatim and "we have no rates for this property"
  is a statement about our data, not about the hotel (§3.3a).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.core.vat import to_vat_inclusive
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    AccommodationSupplement,
    MealPlan,
    RoomType,
)
from app.modules.currency.fx import AdminExchangeRateProvider
from app.modules.park_fees.models import ParkFee
from app.modules.pricing.service import PricingConfigService
from app.modules.quotes import transport as transport_rules
from app.modules.quotes.cohorts import (
    CostLine,
    Group,
    GroupPrice,
    multiplier,
    price_group,
)
from app.modules.quotes.group import build_group, residence_ids
from app.modules.quotes.models import (
    Quote,
    QuoteOption,
    QuoteRejectedCandidate,
    QuoteTransportSegment,
)
from app.modules.quotes.options import (
    BuildUp,
    build_up,
    costed_rate,
    meals_needing_chef,
    meets_minimum_stay,
    minimum_stay_reason,
    needs_chef,
    rate_for_occupancy,
    resolve_meal_plan,
    stay_nights,
    supplement_cost,
    supplier_paid,
)
from app.modules.quotes.packages import Leg, nights_of
from app.modules.transport.models import DestinationTransportMode, TransferRate

# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CostEntry:
    """One line of the internal costing worksheet (§3.12).

    The client document says what a trip costs; this says *why*, in the form
    somebody can check against the supplier's own paper: an amount, the basis
    it is charged on, the multiplier that was actually applied, and the row it
    came from. A cost you cannot trace to a document is a cost you cannot
    defend when a supplier invoices something else.

    ``unit_amount`` is what enters the price. On a discounted rack rate that is
    neither the sheet figure nor what we pay — half the concession is passed to
    the client (§3.5) — so all three are kept: the sheet to reconcile against
    the PDF, the paid figure to reconcile against the invoice, and the costed
    one to reconcile against the quote.
    """

    label: str
    #: Which component of the build-up this rolls into.
    component: str
    basis: str
    unit_amount: Decimal
    #: The multiplier applied — room-nights, person-days, tickets, vehicles.
    quantity: int
    currency: str
    #: ``unit_amount × quantity``, still in ``currency``.
    extended: Decimal
    #: Where it came from: table, row and the supplier document behind it.
    source: str
    residence: str | None = None
    traveller_type: str | None = None
    #: Which leg of the package, for a multi-destination option.
    leg: int | None = None
    #: The sheet rate and what the supplier is actually paid, where they differ
    #: from ``unit_amount`` (a discounted rack rate — see §3.5).
    sheet_amount: Decimal | None = None
    paid_amount: Decimal | None = None


@dataclass(frozen=True)
class SupplementCharge:
    """One mandatory supplement as it applies to this stay."""

    label: str
    kind: str
    basis: str
    amount: Decimal
    currency: str
    nights: int
    cost: Decimal
    #: The multiplier applied, and the row it came from — for the worksheet.
    units: int = 1
    source: str = ""


@dataclass(frozen=True)
class TransportCharge:
    """One movement, costed (§3.10).

    Both the tariff's own figure and the multiplied-out cost are kept. The
    document has to be able to say "SGR economy, KES 1,500 per person, 25
    people" rather than only the product, because a client who queries a fare
    is querying the unit price and not the total.
    """

    sequence: int
    kind: str
    mode: str
    label: str
    basis: str
    units: int
    unit_amount: Decimal
    currency: str
    #: ``unit_amount`` multiplied out against the group, in ``currency``.
    cost: Decimal
    is_optional: bool = False
    is_vvip: bool = False
    #: The tariff row behind it, for the worksheet (§3.12).
    source: str = ""


@dataclass
class TransportCosting:
    """A quote's transport, priced once and charged into every option.

    Transport belongs to the *journey*, not to the hotel, so the same figure
    enters every option's build-up. That is what keeps the comparison between
    options a comparison of the beds, which is the only thing that differs.
    """

    #: Package transport in the presentation currency — what enters the price.
    total: Decimal = Decimal(0)
    #: Add-ons (VVIP and the rest), quoted separately and never in the package.
    optional_total: Decimal = Decimal(0)
    lines: list[CostLine] = field(default_factory=list)
    charges: list[TransportCharge] = field(default_factory=list)
    optional: list[TransportCharge] = field(default_factory=list)
    #: Flights: named on the itinerary, never priced. See the rules module.
    named: list[str] = field(default_factory=list)
    #: Movements with no tariff on file. Blocking at readiness — a movement
    #: priced at zero is the exact failure this stage exists to prevent.
    unpriced: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entries: list[CostEntry] = field(default_factory=list)
    #: What the add-ons sell for: ``optional_total`` through the same build-up
    #: as the package, because an add-on offered at cost is sold at a loss.
    optional_price: Decimal = Decimal(0)


@dataclass
class RoomTypeQuote:
    """The cost of housing the group in one room type for the whole stay."""

    room_type_id: uuid.UUID
    room_type_name: str
    rooms: int
    room_plan: list[int]
    # Costed and paid totals, already in the quote's presentation currency.
    costed_total: Decimal
    paid_total: Decimal
    retained_discount: Decimal
    # Occupancies that had no rate of their own and were priced off a larger
    # room, so a derived figure is never mistaken for a quoted one (§3.3).
    derived_occupancies: tuple[int, ...] = ()
    warnings: list[str] = field(default_factory=list)
    # The worksheet's accommodation lines (§3.12), one per rate row and
    # occupancy actually used rather than one per night: a three-night stay in
    # thirteen rooms is two lines an operator can check, not thirty-nine.
    entries: list[CostEntry] = field(default_factory=list)
    # The same cost split by residency, in the currency each was quoted in —
    # ``{residence: {currency: amount}}``. ``costed_total`` above is this summed
    # and converted, which is what compares room types; this is what lets each
    # residency be priced in its own currency (§3.8).
    costed_by_residence: dict[str, dict[str, Decimal]] = field(default_factory=dict)


@dataclass
class LegCosting:
    """One leg of a package, costed (§3.9).

    A single-property option is a package of one, so this is what every option
    is built from — there is no separate path for the common case to drift from.
    """

    sequence: int
    accommodation_id: uuid.UUID
    accommodation_name: str
    destination_id: uuid.UUID
    room: RoomTypeQuote
    plan: MealPlan
    plan_code: str
    requested_plan: str
    is_fallback: bool
    nights: int
    components: dict[str, Decimal]
    supplements: list[SupplementCharge]
    park_lines: list[CostLine]
    warnings: list[str] = field(default_factory=list)
    entries: list[CostEntry] = field(default_factory=list)


@dataclass
class OptionCosting:
    """One priced option: everything behind it, plus the two client figures."""

    # Which option row this costing belongs to. Matching back by accommodation
    # stopped being unique once an option became a package (§3.9): two curated
    # packages can share their first property and differ later on.
    option_id: uuid.UUID
    accommodation_id: uuid.UUID
    accommodation_name: str
    room_type_id: uuid.UUID
    room_type_name: str
    meal_plan_id: uuid.UUID
    meal_plan_code: str
    # The human label ("Full Board"), carried alongside the code so the
    # document prints what a client reads rather than a two-letter code.
    meal_plan_name: str
    meal_plan_fallback_from: str | None
    rooms_required: int
    nights: int
    currency: str
    components: dict[str, Decimal]
    supplements: list[SupplementCharge]
    supplier_paid_total: Decimal
    retained_discount: Decimal
    build_up: BuildUp
    is_comparable: bool
    warnings: list[str] = field(default_factory=list)
    # What each cohort pays, in its own billing currency (§3.8) — residents in
    # shillings, non-residents in dollars, children apart from adults. The
    # client's confirmed requirement, and the only figures that are meaningful
    # for a mixed group, where ``build_up.per_person`` is necessarily NULL.
    cohort_prices: GroupPrice | None = None
    # Every cost line behind this option, in the order the build-up reads
    # (§3.12). The mirror of the client document: what the page shows as one
    # figure, this shows as the lines it was made of.
    entries: list[CostEntry] = field(default_factory=list)
    # Every leg of the package, in itinerary order. One entry for a
    # single-property option. The top-level ``room_type_*`` and ``meal_plan_*``
    # fields above describe the FIRST leg, because a document needs something to
    # print on one line; a package's real detail is here.
    legs: list[LegCosting] = field(default_factory=list)


# The one fee type an accommodation option implies. Conservancy, camping and the
# rest attach to an activity or a leg rather than to a bed, so they are not
# charged here — see the 3.9 packages work.
PARK_ENTRY = "park_entry"


def _rate_source(rate: AccommodationRate) -> str:
    """Where one accommodation rate came from, as an operator would check it.

    The season and the rate kind matter as much as the id: reconciling against
    a supplier's PDF means finding the right table on the right page, and "STO,
    festive, from 2026-12-20" is what finds it.
    """
    parts = [
        f"accommodation_rates {rate.id}",
        f"{rate.rate_kind.upper()} · {rate.season_name} from {rate.effective_from}",
    ]
    if rate.supplier_discount_pct:
        parts.append(f"sheet discount {rate.supplier_discount_pct}%")
    if rate.source_document_id is not None:
        parts.append(f"document {rate.source_document_id}")
    return " · ".join(parts)


def _supersedes(
    candidate: AccommodationRate, current: AccommodationRate, presentation: str
) -> bool:
    """Whether ``candidate`` should replace ``current`` for one room-night.

    Two separate tiebreaks, in order:

    1. **Currency.** Since §3.12 a property may publish one room-night in KES,
       USD and EUR — the same price quoted three ways, for the agent to bill in
       whichever the client is invoiced in. The one matching the quote's
       presentation currency wins, so no FX conversion (or its rounding) enters
       the client's figure. Without this the winner would be whichever row the
       database happened to return first, which could be the EUR one, for which
       there may be no exchange rate on file at all.
    2. **Season.** Otherwise the later ``effective_from`` wins, so a rate loaded
       to supersede another does.
    """
    matches = candidate.currency.upper() == presentation
    already = current.currency.upper() == presentation
    if matches != already:
        return matches
    return candidate.effective_from > current.effective_from


@dataclass(frozen=True)
class RejectedOption:
    """A property considered but not offered, with wording safe to print."""

    accommodation_id: uuid.UUID
    name: str
    reason: str


@dataclass
class OptionPricingResult:
    options: list[OptionCosting] = field(default_factory=list)
    rejected: list[RejectedOption] = field(default_factory=list)
    # Internal only: why a property could not be priced at all. Never rendered.
    warnings: list[str] = field(default_factory=list)
    # The journey (§3.10). One per quote, not one per option: it is the same
    # journey whichever hotel is chosen, and holding it per option would invite
    # someone to make it differ. Its total is inside every option's build-up.
    transport: TransportCosting = field(default_factory=lambda: TransportCosting())
    # How many people are travelling, as :mod:`app.modules.quotes.group`
    # decided it — cohorts, else pax_count, else the traveller rows. Carried
    # out of pricing because a quote given cohorts has no ``pax_count`` at all,
    # and anything downstream that reaches for that column reads zero.
    pax: int = 0


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class OptionPricingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.fx = AdminExchangeRateProvider(db)

    # -- entry points -------------------------------------------------------- #

    async def compute(self, quote: Quote) -> OptionPricingResult:
        """Price every option on a quote without writing anything."""
        if not quote.options:
            raise AppError(
                "This quote has no options to price. Add the properties to offer "
                "before pricing."
            )
        nights = stay_nights(quote.arrival_date, quote.departure_date)
        # The group vector (§3.8) — cohorts if the quote has them, else its
        # pax_count, else its traveller rows. One place decides, so rooming and
        # the per-person divisor can no longer come from different counts.
        group = await build_group(self.db, quote)
        requested_plan = await self._requested_plan_code(quote)
        cfg = await PricingConfigService(self.db).get()
        contingency = (
            quote.contingency_pct
            if quote.contingency_pct is not None
            else cfg.contingency_pct
        )
        profit = quote.profit_pct if quote.profit_pct is not None else cfg.profit_pct
        # The journey is priced once, not once per option: it is the same
        # journey whichever hotel is chosen, and one lookup per quote also means
        # every option is charged transport at the same fares (§3.10).
        transport = await self._transport(quote, group=group)
        if transport.optional_total:
            # An add-on is priced through the same build-up as the package, so a
            # VVIP upgrade offered separately still carries contingency and
            # margin. No agent cover fee: that is charged once on the quote, not
            # again on each extra.
            transport.optional_price = build_up(
                components={"transport": transport.optional_total},
                pax=group.pax,
                contingency_pct=contingency,
                profit_pct=profit,
                # The presentation currency's own step: a dollar quote whose
                # add-on rounds to the next hundred dollars is not quoting an
                # upgrade, it is quoting a different upgrade (§3.6).
                rounding_step=cfg.rounding_for(quote.presentation_currency),
                uniform_group=group.is_uniform,
            ).group_total

        result = OptionPricingResult(transport=transport, pax=group.pax)
        for option in sorted(quote.options, key=lambda o: o.sort_order):
            await self._price_one(
                quote,
                option,
                nights=nights,
                group=group,
                requested_plan=requested_plan,
                contingency_pct=contingency,
                profit_pct=profit,
                rounding=cfg.rounding_for,
                transport=transport,
                into=result,
            )
        return result

    async def price_options(self, quote_id: uuid.UUID) -> OptionPricingResult:
        """Price the options and persist what was resolved.

        The resolved room type, meal plan and rooming are written back onto each
        ``quote_options`` row, and the rejected candidates are replaced wholesale
        — re-pricing after a date change must not leave the previous run's
        refusals on the document. The money itself is not stored here: it belongs
        in an immutable version snapshot, which document assembly appends.
        """
        quote = await self._load(quote_id)
        result = await self.compute(quote)

        by_option = {c.option_id: c for c in result.options}
        for option in quote.options:
            costing = by_option.get(option.id)
            if costing is None:
                continue
            option.room_type_id = costing.room_type_id
            option.meal_plan_id = costing.meal_plan_id
            option.rooms_required = costing.rooms_required
            option.meal_plan_fallback_from = costing.meal_plan_fallback_from
            option.is_comparable = costing.is_comparable
            # And what each leg resolved to. A package's document prints per leg,
            # so the resolution has to be recorded per leg — the option-level
            # fields describe the first one only.
            per_sequence = {one.sequence: one for one in costing.legs}
            for leg in option.legs:
                resolved = per_sequence.get(leg.sequence)
                if resolved is None:
                    continue
                leg.room_type_id = resolved.room.room_type_id
                leg.meal_plan_id = resolved.plan.id
                leg.rooms_required = resolved.room.rooms
                leg.meal_plan_fallback_from = (
                    resolved.requested_plan if resolved.is_fallback else None
                )

        # Only the engine's own refusals are replaced. An agent's typed one — the
        # reference document's "Diani Cottages, caps at 16 guests" — is not
        # rediscoverable from the rates, so re-pricing must leave it alone.
        stale = (
            (
                await self.db.execute(
                    select(QuoteRejectedCandidate).where(
                        QuoteRejectedCandidate.quote_id == quote.id,
                        QuoteRejectedCandidate.source == "engine",
                    )
                )
            )
            .scalars()
            .all()
        )
        for previous in stale:
            await self.db.delete(previous)
        for order, refused in enumerate(result.rejected, start=1):
            self.db.add(
                QuoteRejectedCandidate(
                    quote_id=quote.id,
                    accommodation_id=refused.accommodation_id,
                    name=refused.name,
                    reason=refused.reason,
                    sort_order=order,
                    source="engine",
                )
            )
        await self.db.commit()
        return result

    # -- one option ---------------------------------------------------------- #

    async def _price_leg(
        self,
        quote: Quote,
        option: QuoteOption,
        *,
        accommodation_id: uuid.UUID,
        nights: list[date],
        group: Group,
        requested_plan: str,
        sequence: int,
        into: OptionPricingResult,
    ) -> LegCosting | None:
        """Cost one leg: pick the room type, resolve the plan, gather the extras.

        This is the whole of what used to be an option. A package is an ordered
        list of these (§3.9), and the only thing that changes per leg is the
        property, the nights and the plan the agent asked for *for that leg* — so
        the same code serves a one-hotel quote and a three-destination trip, and
        there is no second implementation to drift.
        """
        pax = group.pax
        accommodation = await self.db.get(Accommodation, accommodation_id)
        if accommodation is None:
            raise NotFoundError(
                "An option references an accommodation that no longer exists."
            )

        rates = await self._rates(quote, accommodation.id, nights, group)
        if not rates:
            into.warnings.append(
                f"{accommodation.name}: no rates are loaded for "
                f"{nights[0]} to {nights[-1]} for "
                f"{' or '.join(group.residences)}, so it could not be priced."
            )
            return None

        # A plan is only usable if EVERY residency on the quote has a rate on it.
        # A property that prices non-residents on full board and residents on
        # bed and breakfast only cannot offer one comparable board basis to a
        # mixed group, and quietly pricing each half on a different plan would
        # put two different holidays on one line.
        by_residence: dict[str, set[str]] = {}
        for (_room, code, residence) in rates:
            by_residence.setdefault(residence, set()).add(code)
        available = set.intersection(*by_residence.values()) if by_residence else set()
        if not available and len(group.residences) > 1:
            into.warnings.append(
                f"{accommodation.name}: no meal plan has a rate for every "
                f"residency on this quote ("
                + "; ".join(
                    f"{res}: {'/'.join(sorted(codes))}"
                    for res, codes in sorted(by_residence.items())
                )
                + "), so it could not be priced for this group."
            )
            return None
        plan_code, is_fallback = resolve_meal_plan(requested_plan, available)
        if plan_code is None:
            into.warnings.append(
                f"{accommodation.name}: no rate on any plan in the "
                f"{requested_plan} fallback chain, so it could not be priced."
            )
            return None
        plan = await self._plan_by_code(plan_code)

        best: RoomTypeQuote | None = None
        shortfalls: list[int] = []
        room_type_ids = {room for (room, code, _res) in rates if code == plan_code}
        for room_type_id in room_type_ids:
            attempt = await self._price_room_type(
                quote,
                room_type_id,
                {
                    residence: rates[(room_type_id, plan_code, residence)]
                    for residence in group.residences
                    if (room_type_id, plan_code, residence) in rates
                },
                nights=nights,
                group=group,
            )
            if attempt is None:
                continue
            candidate, shortfall = attempt
            if shortfall is not None:
                shortfalls.append(shortfall)
                continue
            assert candidate is not None
            if best is None or candidate.costed_total < best.costed_total:
                best = candidate

        if best is None:
            if shortfalls:
                # Every room type that could house the group needs a longer stay,
                # so the property is not offerable — but it is still shown as
                # considered (§3.3a). The shortest minimum is the honest one to
                # quote back, since it is the easiest for the client to meet.
                into.rejected.append(
                    RejectedOption(
                        accommodation_id=accommodation.id,
                        name=accommodation.name,
                        reason=minimum_stay_reason(len(nights), min(shortfalls)),
                    )
                )
            else:
                # A gap in the season windows and a gap in the occupancies both
                # end with no priceable room type, and they need completely
                # different fixes — one is a missing row in the sheet, the other
                # is a property that cannot take the group. Saying "no room type
                # could house 2 guests" when the truth is "nobody priced 31
                # October" sends an agent hunting for the wrong thing. Swahili
                # Beach's real sheet has exactly that hole: HIGH ends 30 Oct and
                # SHOULDER starts 1 Nov.
                uncovered = [
                    night
                    for night in nights
                    if not any(
                        night in per_night
                        for (room, code, _res), per_night in rates.items()
                        if code == plan_code
                    )
                ]
                if uncovered:
                    into.warnings.append(
                        f"{accommodation.name}: no {plan_code} rate is loaded for "
                        f"{', '.join(str(night) for night in uncovered)} — a gap "
                        f"between season windows, not a capacity problem. The "
                        f"property is priceable either side of it."
                    )
                else:
                    into.warnings.append(
                        f"{accommodation.name}: no room type could house {pax} "
                        f"guests at the rates on file, so it could not be priced."
                    )
            return None

        supplements = await self._supplements(
            quote,
            accommodation.id,
            room_type_id=best.room_type_id,
            meal_plan_id=plan.id,
            nights=nights,
            pax=pax,
            rooms=best.rooms,
        )

        components: dict[str, Decimal] = {"accommodation": best.costed_total}
        if supplements:
            components["supplements"] = sum((s.cost for s in supplements), Decimal(0))

        warnings = list(best.warnings)
        park_total, park_warnings, park_lines, park_entries = await self._park_fees(
            quote, accommodation, nights=nights, group=group
        )
        if park_total:
            components["park_fees"] = park_total
        warnings.extend(park_warnings)
        if needs_chef(plan_code):
            meals = meals_needing_chef(plan_code, len(nights))
            chef = (option.chef_fee_per_meal or Decimal(0)) * meals
            food = option.manual_meal_cost or Decimal(0)
            if chef:
                components["chef"] = chef
            if food:
                components["meals"] = food
            if not chef or not food:
                warnings.append(
                    f"priced on {plan_code}, which needs a chef and a food cost "
                    f"for {meals} group meal(s); set chef_fee_per_meal and "
                    f"manual_meal_cost on this option before issuing it"
                )
        if is_fallback:
            warnings.append(
                f"the client asked for {requested_plan} and this property has no "
                f"{requested_plan} rate, so it is priced on {plan_code}"
            )

        # The leg's worksheet lines, in the order the build-up reads them.
        entries: list[CostEntry] = [
            replace(entry, leg=sequence) for entry in best.entries
        ]
        entries.extend(
            CostEntry(
                label=charge.label,
                component="supplements",
                basis=charge.basis,
                unit_amount=charge.amount,
                quantity=charge.units,
                currency=charge.currency,
                extended=charge.amount * charge.units,
                source=charge.source,
                leg=sequence,
            )
            for charge in supplements
        )
        entries.extend(replace(entry, leg=sequence) for entry in park_entries)
        if needs_chef(plan_code):
            meals = meals_needing_chef(plan_code, len(nights))
            if option.chef_fee_per_meal:
                entries.append(
                    CostEntry(
                        label="Chef fee",
                        component="chef",
                        basis="per_group",
                        unit_amount=option.chef_fee_per_meal,
                        quantity=meals,
                        currency=quote.presentation_currency.upper(),
                        extended=option.chef_fee_per_meal * meals,
                        # Entered by an agent, so the "source" is the agent:
                        # there is no supplier document to reconcile against,
                        # and saying so is the point.
                        source="quote_options.chef_fee_per_meal (entered by hand)",
                        leg=sequence,
                    )
                )
            if option.manual_meal_cost:
                entries.append(
                    CostEntry(
                        label="Group food cost",
                        component="meals",
                        basis="per_group",
                        unit_amount=option.manual_meal_cost,
                        quantity=1,
                        currency=quote.presentation_currency.upper(),
                        extended=option.manual_meal_cost,
                        source="quote_options.manual_meal_cost (entered by hand)",
                        leg=sequence,
                    )
                )

        return LegCosting(
            sequence=sequence,
            accommodation_id=accommodation.id,
            accommodation_name=accommodation.name,
            destination_id=accommodation.destination_id,
            room=best,
            plan=plan,
            plan_code=plan_code,
            requested_plan=requested_plan,
            is_fallback=is_fallback,
            nights=len(nights),
            components=components,
            supplements=supplements,
            park_lines=park_lines,
            warnings=warnings,
            entries=entries,
        )

    async def _legs_of(
        self,
        option: QuoteOption,
        quote: Quote,
        nights: list[date],
        requested_plan: str,
    ) -> list[tuple[int, uuid.UUID, list[date], str]] | None:
        """The legs to price, as ``(sequence, accommodation, nights, plan)``.

        An option with no leg rows is a package of one covering the whole stay —
        the same precedence the group vector uses over ``pax_count``, so there is
        one answer to "what is this option?" rather than two that can disagree.

        Contiguity was already enforced when the package was stored, so this
        trusts the dates; what it will not do is *derive* a leg's nights from a
        count, because a stored date is the only thing that survives an edit.
        """
        if not option.legs:
            return [(1, option.accommodation_id, nights, requested_plan)]

        plans = {
            row.id: row.code.upper()
            for row in (await self.db.execute(select(MealPlan))).scalars().all()
        }
        out: list[tuple[int, uuid.UUID, list[date], str]] = []
        for leg in sorted(option.legs, key=lambda entry: entry.sequence):
            # Meal plan is a per-leg decision (§3.9): a day out of the hotel
            # makes half board the right plan rather than a fallback from full
            # board, and the two have to be distinguishable on the document.
            plan = plans.get(leg.requested_meal_plan_id or uuid.UUID(int=0))
            out.append(
                (
                    leg.sequence,
                    leg.accommodation_id,
                    nights_of(
                        Leg(
                            sequence=leg.sequence,
                            destination=str(leg.destination_id),
                            check_in=leg.check_in,
                            check_out=leg.check_out,
                        )
                    ),
                    plan or requested_plan,
                )
            )
        return out

    # -- one option, which is one or more legs ------------------------------- #

    async def _price_one(
        self,
        quote: Quote,
        option: QuoteOption,
        *,
        nights: list[date],
        group: Group,
        requested_plan: str,
        contingency_pct: Decimal,
        profit_pct: Decimal,
        # A step *per currency*, not one number: the option's own build-up
        # rounds in the presentation currency and each cohort rounds in its
        # own (§3.8), and KES 100 against USD 1 is the difference between a
        # rounding and a 48% mark-up.
        rounding: Callable[[str], Decimal],
        transport: TransportCosting,
        into: OptionPricingResult,
    ) -> None:
        """Price a curated package — one property per leg (§3.9).

        A single-property option is a package of one, priced by exactly the same
        path, so nothing about the common case is special-cased.

        The legs are summed rather than compared: they are one offer, not
        alternatives. If any leg cannot be priced the whole package is dropped,
        because half a trip is not something to put in front of a client.
        """
        legs = await self._legs_of(option, quote, nights, requested_plan)
        if legs is None:
            return

        costed: list[LegCosting] = []
        for sequence, accommodation_id, leg_nights, leg_plan in legs:
            one = await self._price_leg(
                quote,
                option,
                accommodation_id=accommodation_id,
                nights=leg_nights,
                group=group,
                requested_plan=leg_plan,
                sequence=sequence,
                into=into,
            )
            if one is None:
                # Already warned about by _price_leg. A package missing a leg is
                # not a cheaper package, it is an incomplete one.
                if len(legs) > 1:
                    into.warnings.append(
                        f"Package option {option.sort_order}: leg {sequence} "
                        f"could not be priced, so the whole package was dropped."
                    )
                return
            costed.append(one)

        lead = costed[0]
        components: dict[str, Decimal] = {}
        for one in costed:
            for label, amount in one.components.items():
                components[label] = components.get(label, Decimal(0)) + amount
        # The journey enters every option identically, so what the client
        # compares is the beds — the only thing that actually differs (§3.10).
        if transport.total:
            components["transport"] = transport.total

        # The room cost split by residency, merged across legs, so each residency
        # is still priced off its own sheets in its own currency (§3.8).
        merged_by_residence: dict[str, dict[str, Decimal]] = {}
        for one in costed:
            for residence, buckets in one.room.costed_by_residence.items():
                target = merged_by_residence.setdefault(residence, {})
                for currency, amount in buckets.items():
                    target[currency] = target.get(currency, Decimal(0)) + amount

        park_lines = [line for one in costed for line in one.park_lines]
        # The whole option's worksheet: its legs' lines in itinerary order,
        # then the journey, which belongs to the quote and is charged into
        # every option (§3.10).
        entries = [entry for one in costed for entry in one.entries]
        entries.extend(transport.entries)
        supplements = [charge for one in costed for charge in one.supplements]
        warnings = [
            (f"leg {one.sequence} ({one.accommodation_name}): {note}"
             if len(costed) > 1 else note)
            for one in costed
            for note in one.warnings
        ]
        warnings.extend(transport.warnings)
        paid_total = sum((one.room.paid_total for one in costed), Decimal(0))
        costed_total = sum((one.room.costed_total for one in costed), Decimal(0))

        totals = build_up(
            components=components,
            pax=group.pax,
            contingency_pct=contingency_pct,
            profit_pct=profit_pct,
            agent_cover_fee=option.agent_cover_fee,
            rounding_step=rounding(quote.presentation_currency),
            # A per-person figure is only meaningful when everyone pays the same.
            # The vector is what finally makes residency part of that judgement:
            # before it, only traveller type could vary, so a mixed-residency
            # group got one per-person figure covering two currencies.
            uniform_group=group.is_uniform,
        )
        # ``best`` is handed the merged per-residency split, so a package's
        # accommodation lines reach the right cohorts across every leg.
        merged = replace(lead.room, costed_by_residence=merged_by_residence)
        cohort_prices = await self._per_cohort(
            quote,
            merged,
            direct_lines=park_lines + transport.lines,
            shared=components,
            capacity_of=lead.room.room_type_id,
            group=group,
            nights=nights,
            contingency_pct=contingency_pct,
            profit_pct=profit_pct,
            agent_cover_fee=option.agent_cover_fee,
            rounding=rounding,
        )
        into.options.append(
            OptionCosting(
                option_id=option.id,
                accommodation_id=lead.accommodation_id,
                accommodation_name=(
                    lead.accommodation_name
                    if len(costed) == 1
                    else " → ".join(one.accommodation_name for one in costed)
                ),
                room_type_id=lead.room.room_type_id,
                room_type_name=lead.room.room_type_name,
                meal_plan_id=lead.plan.id,
                meal_plan_code=lead.plan_code,
                meal_plan_name=lead.plan.name,
                meal_plan_fallback_from=(
                    lead.requested_plan if lead.is_fallback else None
                ),
                # The most rooms the package needs at any point. Legs are
                # sequential, not simultaneous, so summing them would book a
                # room in Diani for a night spent in the Mara.
                rooms_required=max(one.room.rooms for one in costed),
                nights=sum(one.nights for one in costed),
                currency=quote.presentation_currency.upper(),
                components=components,
                supplements=supplements,
                supplier_paid_total=paid_total,
                retained_discount=costed_total - paid_total,
                build_up=totals,
                # An option priced on a different board basis is not comparable
                # with the others; an agent flag can only narrow that, not widen it.
                is_comparable=(
                    option.is_comparable
                    and not any(one.is_fallback for one in costed)
                ),
                warnings=warnings,
                cohort_prices=cohort_prices,
                entries=entries,
                legs=costed,
            )
        )

    async def _price_room_type(
        self,
        quote: Quote,
        room_type_id: uuid.UUID,
        per_residence: dict[str, dict[date, dict[int, AccommodationRate]]],
        *,
        nights: list[date],
        group: Group,
    ) -> tuple[RoomTypeQuote | None, int | None] | None:
        """Cost the whole stay in one room type, residency by residency.

        Returns ``None`` when the room type cannot house the group at all, and
        ``(None, min_nights)`` when it could but the stay is too short. Keeping
        those two apart is what lets the caller tell a client-facing refusal
        (§3.3a) from an internal gap in the data.

        Rooming partitions by **residency**, so each residency's rooms are
        costed against its own rate sheet in its own currency (§3.8). Three
        residents and three non-residents therefore take four twins rather than
        three: no room can hold one of each and still have a defined rate. The
        extra room is the honest cost of a mixed group being quotable, and it
        appears here rather than being discovered at check-in.
        """
        room_type = await self.db.get(RoomType, room_type_id)
        if room_type is None or not room_type.is_active:
            return None

        rooming = group.rooming(room_type.max_occupancy)
        plan: list[int] = []
        paid: dict[str, Decimal] = {}
        costed: dict[str, Decimal] = {}
        by_residence: dict[str, dict[str, Decimal]] = {}
        derived: set[int] = set()
        per_person_basis: set[str] = set()
        # (rate row, guests in the room, residency) -> room-nights charged.
        used_rates: dict[tuple[uuid.UUID, int, str], list] = {}
        for residence, rooms in rooming.items():
            nightly = per_residence.get(residence)
            if not nightly:
                # This residency has no rate on this room type. Dropping the
                # room type is right: pricing the rest of the group in it and
                # silently housing these travellers somewhere else is not a
                # quote anybody could honour.
                return None
            plan.extend(rooms)
            for night in nights:
                by_occupancy = nightly.get(night)
                if not by_occupancy:
                    return None
                for guests in rooms:
                    found = rate_for_occupancy(by_occupancy, guests)
                    if found is None:
                        return None
                    used, rate = found
                    if not meets_minimum_stay(len(nights), rate.min_nights):
                        assert rate.min_nights is not None
                        return None, rate.min_nights
                    if used != guests:
                        derived.add(guests)
                        if rate.single_supplement is not None:
                            per_person_basis.add(room_type.name)
                    amount = rate.rate_per_night
                    currency = rate.currency.upper()
                    paid[currency] = paid.get(currency, Decimal(0)) + supplier_paid(
                        amount, rate.supplier_discount_pct
                    )
                    charge = costed_rate(
                        amount, rate.supplier_discount_pct, rate.rate_kind
                    )
                    costed[currency] = costed.get(currency, Decimal(0)) + charge
                    mine = by_residence.setdefault(residence, {})
                    mine[currency] = mine.get(currency, Decimal(0)) + charge
                    slot = (rate.id, guests, residence)
                    if slot in used_rates:
                        used_rates[slot][0] += 1
                    else:
                        used_rates[slot] = [1, rate]

        warnings: list[str] = []
        if derived:
            warnings.append(
                f"{room_type.name}: no rate is quoted for "
                f"{', '.join(str(o) for o in sorted(derived))}-guest occupancy, "
                f"so the next larger room's price was charged in full"
            )
        if per_person_basis:
            # §3.3 gives "single supplement on top of the shared rate" as the
            # fallback where a sheet quotes no single. That addition is only
            # coherent on a sheet priced PER PERSON SHARING: adding it to a
            # per-room rate would charge one guest 28,000 for the same room two
            # guests pay 24,000 for. Our rates are stored per room, so the room
            # is charged in full instead — which is the other half of the same
            # rule ("an odd single room is charged in full, not half") — and the
            # stated supplement is surfaced for review rather than applied
            # silently, because its presence is a hint the sheet may be
            # per-person and was ingested on the wrong basis.
            warnings.append(
                f"{room_type.name}: the sheet states a single supplement but no "
                f"single-occupancy rate. The room was charged in full; check "
                f"whether this sheet prices per person sharing before issuing."
            )
        entries = [
            CostEntry(
                label=f"{room_type.name}, {guests} sharing ({residence})",
                component="accommodation",
                basis="per_room_per_night",
                unit_amount=costed_rate(
                    rate.rate_per_night, rate.supplier_discount_pct, rate.rate_kind
                ),
                quantity=room_nights,
                currency=rate.currency.upper(),
                extended=costed_rate(
                    rate.rate_per_night, rate.supplier_discount_pct, rate.rate_kind
                )
                * room_nights,
                source=_rate_source(rate),
                residence=residence,
                sheet_amount=rate.rate_per_night,
                paid_amount=supplier_paid(
                    rate.rate_per_night, rate.supplier_discount_pct
                ),
            )
            for (_, guests, residence), (room_nights, rate) in used_rates.items()
        ]
        paid_total = await self._to_presentation(quote, paid)
        costed_total = await self._to_presentation(quote, costed)
        return (
            RoomTypeQuote(
                room_type_id=room_type.id,
                room_type_name=room_type.name,
                rooms=len(plan),
                room_plan=plan,
                costed_total=costed_total,
                paid_total=paid_total,
                retained_discount=costed_total - paid_total,
                derived_occupancies=tuple(sorted(derived)),
                warnings=warnings,
                costed_by_residence=by_residence,
                entries=entries,
            ),
            None,
        )

    # -- lookups ------------------------------------------------------------- #

    async def _requested_plan_code(self, quote: Quote) -> str:
        if quote.requested_meal_plan_id is None:
            raise AppError(
                "Set requested_meal_plan_id on the quote before pricing options — "
                "the fallback chain has no starting point without it."
            )
        plan = await self.db.get(MealPlan, quote.requested_meal_plan_id)
        if plan is None:
            raise NotFoundError("The quote's requested meal plan no longer exists.")
        return plan.code.upper()

    async def _plan_by_code(self, code: str) -> MealPlan:
        plan = (
            await self.db.execute(select(MealPlan).where(MealPlan.code == code))
        ).scalar_one_or_none()
        if plan is None:  # pragma: no cover - the code came from a joined row
            raise NotFoundError(f"Meal plan {code} not found.")
        return plan

    async def _rates(
        self,
        quote: Quote,
        accommodation_id: uuid.UUID,
        nights: list[date],
        group: Group,
    ) -> dict[
        tuple[uuid.UUID, str, str], dict[date, dict[int, AccommodationRate]]
    ]:
        """Every usable rate, indexed by room type, plan, residency, night, occupancy.

        One query for the whole property, then indexed in Python: a ten-night
        stay across four room types and three occupancies would otherwise be 120
        round trips to answer one question.

        Where two rates overlap a night, the later ``effective_from`` wins — the
        same tiebreak :class:`AccommodationRateService` uses, so a rate loaded to
        supersede another does so here too.

        Fetched for **every residency on the quote**, not just the quote's own
        category: a mixed group is priced off two sheets in two currencies, and
        one query for all of them keeps the round trips at one per property.

        Where a property publishes the same room-night in more than one currency
        (§3.12), the one matching the quote's presentation currency wins, because
        billing in the currency the supplier quoted removes an FX conversion —
        and its rounding — from the client's figure entirely.
        """
        keys = group.residences
        ids = await residence_ids(self.db, keys)
        by_id = {value: name for name, value in ids.items()}
        stmt = (
            select(AccommodationRate, MealPlan.code)
            .join(MealPlan, MealPlan.id == AccommodationRate.meal_plan_id)
            .join(RoomType, RoomType.id == AccommodationRate.room_type_id)
            .where(
                RoomType.accommodation_id == accommodation_id,
                RoomType.is_active.is_(True),
                AccommodationRate.residence_category_id.in_(ids.values()),
                AccommodationRate.is_active.is_(True),
                AccommodationRate.effective_from <= nights[-1],
                AccommodationRate.effective_to >= nights[0],
            )
        )
        rows = list((await self.db.execute(stmt)).all())

        presentation = quote.presentation_currency.upper()
        index: dict[
            tuple[uuid.UUID, str, str], dict[date, dict[int, AccommodationRate]]
        ] = {}
        for rate, code in rows:
            residence = by_id.get(rate.residence_category_id)
            if residence is None:
                continue
            key = (rate.room_type_id, code.upper(), residence)
            per_night = index.setdefault(key, {})
            for night in nights:
                if not (rate.effective_from <= night <= rate.effective_to):
                    continue
                slot = per_night.setdefault(night, {})
                current = slot.get(rate.occupancy)
                if current is None or _supersedes(rate, current, presentation):
                    slot[rate.occupancy] = rate
        return index

    async def _per_cohort(
        self,
        quote: Quote,
        best: RoomTypeQuote,
        *,
        direct_lines: list[CostLine],
        shared: dict[str, Decimal],
        capacity_of: uuid.UUID,
        group: Group,
        nights: list[date],
        contingency_pct: Decimal,
        profit_pct: Decimal,
        agent_cover_fee: Decimal,
        rounding: Callable[[str], Decimal],
    ) -> GroupPrice | None:
        """What each cohort pays, in its own billing currency (§3.8).

        The client's requirement in one figure per cohort: residents quoted in
        shillings, non-residents in dollars, children apart from adults, and a
        group total for the whole booking.

        ``direct_lines`` are the costs that already know who bears them and in
        which currency — park fees, and the transport tariffs (§3.10) — so they
        are attributed as they stand rather than re-derived from a presentation
        currency total.

        Three shapes of cost line, and which cohorts each reaches is the whole
        design:

        * **Accommodation** names a residency and no traveller type, so it is
          shared within that residency — a resident adult and a resident child
          sleep in the same rooms off the same sheet.
        * **Park fees** name a residency *and* a type, because a resident child
          pays the resident child rate and nobody else shares that line.
        * **Transport** names neither and is already multiplied out, because a
          seat costs the same whoever is in it.
        * **Supplements, chef and food** name neither, so they split per head
          across the whole group. A chef costs the same whoever eats.

        Those already arrive converted to the presentation currency, which is
        why the converter matters: a shared cost is one amount that has to land
        in two currencies, and :func:`~app.modules.quotes.cohorts.attribute`
        splits it *before* converting so no cohort's share carries its own
        rounding of the exchange rate.
        """
        room_type = await self.db.get(RoomType, capacity_of)
        if room_type is None:
            return None
        presentation = quote.presentation_currency.upper()

        lines: list[CostLine] = [
            CostLine(
                label="accommodation",
                amount=amount,
                currency=currency,
                basis="per_group",
                residence=residence,
            )
            for residence, buckets in best.costed_by_residence.items()
            for currency, amount in buckets.items()
        ]
        lines.extend(direct_lines)
        # Everything the whole group shares. Already in the presentation
        # currency, since that is how the components dict is built. The labels
        # skipped here are the ones already above as direct lines — adding the
        # component total as well would charge them twice.
        for label, amount in shared.items():
            if label in {"accommodation", "park_fees", "transport"} or amount == 0:
                continue
            lines.append(
                CostLine(
                    label=label,
                    amount=amount,
                    currency=presentation,
                    basis="per_group",
                )
            )

        convert, rate_used = await self._converter(
            {line.currency for line in lines}
            | {cohort.currency for cohort in group.cohorts}
            | {presentation},
            on_date=nights[0],
        )
        return price_group(
            lines=lines,
            group=group,
            capacity=room_type.max_occupancy,
            contingency_pct=contingency_pct,
            profit_pct=profit_pct,
            agent_cover_fee=agent_cover_fee,
            # Handed the function rather than a number, so each cohort rounds
            # in the currency it is actually billed in.
            rounding_step=rounding,
            group_currency=presentation,
            convert=convert,
            rate_used=rate_used,
        )

    async def _converter(
        self, currencies: set[str], *, on_date: date
    ) -> tuple[
        Callable[[Decimal, str, str], Decimal], Callable[[str, str], Decimal]
    ]:
        """A *synchronous* converter over a pre-fetched rate table.

        The pure layer takes a plain callable on purpose — it must not do I/O,
        and pinning the rates here means every figure on one option is converted
        at the same rate as every other. Resolving them lazily inside the
        arithmetic would let a rate change mid-quote, and the totals would then
        not reconcile with the per-person figures they were derived from.
        """
        rates: dict[tuple[str, str], Decimal] = {}
        for base in currencies:
            for quote_ccy in currencies:
                if base == quote_ccy:
                    rates[(base, quote_ccy)] = Decimal(1)
                    continue
                try:
                    rates[(base, quote_ccy)] = await self.fx.effective_rate(
                        base, quote_ccy, on_date
                    )
                except (AppError, NotFoundError):
                    # Missing pairs are left out. If a line actually needs one,
                    # the lookup below raises with both currencies named, which
                    # is a better error than a rate silently defaulting to 1.
                    continue

        def rate_for(base: str, quote_ccy: str) -> Decimal:
            try:
                return rates[(base.upper(), quote_ccy.upper())]
            except KeyError:
                raise AppError(
                    f"No exchange rate on file for {base.upper()} to "
                    f"{quote_ccy.upper()} on {on_date}, so this option cannot "
                    "be priced per cohort."
                ) from None

        def convert(amount: Decimal, base: str, quote_ccy: str) -> Decimal:
            return amount * rate_for(base, quote_ccy)

        return convert, rate_for

    async def _park_fees(
        self,
        quote: Quote,
        accommodation: Accommodation,
        *,
        nights: list[date],
        group: Group,
    ) -> tuple[Decimal, list[str], list[CostLine], list[CostEntry]]:
        """Park and conservation entry for this option's destination (§3.8).

        The first time these reach an option's price. They were already on the
        Stage 2.8 leg-based path (:class:`PricingEngine`), but the Stage 3
        multi-option build-up — the one the client's document actually renders —
        omitted them entirely. A three-night Maasai Mara option for twelve
        non-residents was short by twelve times three times the daily fee, which
        is not a rounding error.

        Selected **per night**, like rates and for the same reason (§3.1): the
        Mara publishes two seasons, so a stay crossing the boundary priced once
        would be charged entirely at the cheaper one.

        Charged per person per day, and the rate depends on the cohort's
        traveller type — which is exactly what the vector carries, so no age has
        to be guessed at.

        The limit of that: a cohort is *counts*, so the agent's declared type is
        taken at face value and the park's own child band is not applied. The
        Mara exempts under-6s and charges 6–17, so a four-year-old entered as a
        ``child`` is charged where the schedule would let them in free. It errs
        toward over-charging, which is the visible direction, but it is not the
        published rule; closing it needs ages on the quote. The age-based path
        lives in :mod:`app.modules.park_fees.service`.

        Returns ``(total_in_presentation_currency, warnings)``. A destination
        with no fees at all is silent: most beach properties are not in a park.
        A destination with fees for *some* residencies is a warning, because that
        gap under-charges the residencies it is missing.
        """
        residences = await residence_ids(self.db, group.residences)
        by_id = {value: name for name, value in residences.items()}
        rows = list(
            (
                await self.db.execute(
                    select(ParkFee).where(
                        ParkFee.destination_id == accommodation.destination_id,
                        ParkFee.fee_type == PARK_ENTRY,
                        ParkFee.residence_category_id.in_(residences.values()),
                        ParkFee.is_active.is_(True),
                        ParkFee.effective_from <= nights[-1],
                        ParkFee.effective_to >= nights[0],
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return Decimal(0), [], [], []

        index: dict[tuple[str, date], ParkFee] = {}
        for fee in rows:
            residence = by_id.get(fee.residence_category_id)
            if residence is None:
                continue
            for night in nights:
                if not (fee.effective_from <= night <= fee.effective_to):
                    continue
                current = index.get((residence, night))
                if current is None or fee.effective_from > current.effective_from:
                    index[(residence, night)] = fee

        # Accumulated per cohort as well as per currency, because a fee belongs
        # to the exact cohort that owes it: a resident child pays the resident
        # child rate, and nothing else in the group shares that line.
        per_cohort: dict[tuple[str, str, str], Decimal] = {}
        per_currency: dict[str, Decimal] = {}
        uncovered: set[str] = set()
        # (fee row, cohort) -> [person-days charged, the amount each, the row].
        charged: dict[tuple[uuid.UUID, str, str], list] = {}
        for cohort in group.cohorts:
            for night in nights:
                tonight = index.get((cohort.residence, night))
                if tonight is None:
                    uncovered.add(cohort.residence)
                    continue
                amount = {
                    "adult": tonight.adult,
                    "child": tonight.child,
                    "infant": tonight.infant,
                }.get(cohort.traveller_type, tonight.adult)
                currency = tonight.currency.upper()
                charge = amount * cohort.count
                per_currency[currency] = per_currency.get(currency, Decimal(0)) + charge
                slot = (cohort.residence, cohort.traveller_type, currency)
                per_cohort[slot] = per_cohort.get(slot, Decimal(0)) + charge
                line = (tonight.id, cohort.residence, cohort.traveller_type)
                if line in charged:
                    charged[line][0] += cohort.count
                else:
                    charged[line] = [cohort.count, amount, tonight]

        lines = [
            CostLine(
                label="park_fees",
                amount=charge,
                currency=currency,
                basis="per_group",
                residence=residence,
                traveller_type=traveller_type,
            )
            for (residence, traveller_type, currency), charge in per_cohort.items()
        ]

        warnings: list[str] = []
        if uncovered:
            warnings.append(
                f"{accommodation.name}: this destination charges park fees but "
                f"none is on file for {', '.join(sorted(uncovered))} across the "
                f"whole stay, so those travellers are quoted without them. Load "
                f"the missing schedule before issuing."
            )
        entries = [
            CostEntry(
                label=(
                    f"Park entry, {accommodation.name}'s destination "
                    f"({residence} {traveller_type})"
                ),
                component="park_fees",
                basis="per_person_per_day",
                unit_amount=amount,
                quantity=person_days,
                currency=fee.currency.upper(),
                extended=amount * person_days,
                source=(
                    f"park_fees {fee.id} · {fee.fee_type} from {fee.effective_from}"
                ),
                residence=residence,
                traveller_type=traveller_type,
            )
            for (_, residence, traveller_type), (
                person_days,
                amount,
                fee,
            ) in charged.items()
        ]
        return (
            await self._to_presentation(quote, per_currency),
            warnings,
            lines,
            entries,
        )

    # -- transport ----------------------------------------------------------- #

    @staticmethod
    def _segment_label(segment: QuoteTransportSegment) -> str:
        parts = [
            "Transfer" if segment.kind == transport_rules.TRANSFER else "Line haul",
            segment.mode.upper(),
        ]
        if segment.travel_class:
            parts.append(segment.travel_class.title())
        label = " · ".join(parts)
        return f"{segment.description} ({label})" if segment.description else label

    async def _transport(self, quote: Quote, *, group: Group) -> TransportCosting:
        """Price the journey (§3.8, stage 3.10).

        Transport is charged **into every option** rather than beside them,
        because it is the same journey whichever hotel the client picks: putting
        it outside the options would make the cheapest bed look like the
        cheapest trip, and the client compares trips.

        Three things this will not do.

        It will not **invent a fare**. A movement with no tariff on file is
        recorded as unpriced and blocks at readiness rather than being charged
        at zero, because a zero is indistinguishable on a document from a leg
        the client is genuinely not being charged for.

        It will not **price a flight**. Air is unpriceable, not merely unpriced
        (see the rules module), so a flight segment is named for the itinerary
        and its fare becomes an exclusion.

        It will not price the whole quote at **one instant**. Each movement is
        priced at the tariff effective on its own ``travel_date`` — the
        arrival date where none is given — for the same reason accommodation is
        selected per night: fares move, and a return rail leg after a revision
        is a different price from the outbound one.

        Optional segments are costed but held apart: a VVIP upgrade is an
        add-on, and folding it into the package would change what the options
        are being compared on.
        """
        out = TransportCosting()
        segments = sorted(quote.transport_segments, key=lambda s: s.sequence)
        if not segments:
            return out

        per_currency: dict[str, Decimal] = {}
        optional_currency: dict[str, Decimal] = {}
        for segment in segments:
            label = self._segment_label(segment)
            if segment.mode in transport_rules.NAMED_ONLY_MODES:
                # The agent's own words for the flight, not our composed
                # "Line haul · AIR" label: this string reaches the client, on
                # the transport page and in the exclusions, and "Nairobi to
                # Ukunda (Line haul · AIR)" is our vocabulary leaking again.
                out.named.append(segment.description or label)
                continue
            if (
                segment.mode not in transport_rules.PRICED_MODES
                or segment.kind not in transport_rules.KINDS
            ):
                # The validation rules already refuse this; pricing's job is
                # only not to put a number against it.
                out.unpriced.append(label)
                continue
            # A segment run on our own or a hired vehicle is costed by the
            # Stage 2 fleet model (km, fuel, driver) off ``quote_transport``,
            # not from a transfer tariff, so it is not charged twice here.
            if segment.vehicle_id is not None:
                continue

            on = segment.travel_date or quote.arrival_date
            if not (quote.arrival_date <= on <= quote.departure_date):
                out.warnings.append(
                    f"{label} travels on {on}, which is outside the quote's "
                    f"{quote.arrival_date} to {quote.departure_date} window, so "
                    f"it is priced at that date's tariff. Check the date."
                )
            tariff = await self._tariff_for(segment, on=on)
            if tariff is None:
                out.unpriced.append(label)
                out.warnings.append(
                    f"{label}: no tariff on file for this destination and "
                    f"vehicle on {on}, so the movement carries no price. Load "
                    f"the fare before issuing — a zero here reads on the "
                    f"document as a leg the client is not being charged for."
                )
                continue

            amount, currency, basis, tariff_label, source = tariff
            if (
                segment.kind == transport_rules.TRANSFER
                and segment.description
                and tariff_label
                and tariff_label.strip().casefold()
                != segment.description.strip().casefold()
            ):
                # A transfer tariff is keyed on its route, and one route is not
                # another: town-to-terminus is not terminus-to-hotel. Priced off
                # the nearest row we have rather than left at zero, but said out
                # loud, because a plausible figure for the wrong drive is the
                # kind of error nobody goes looking for.
                out.warnings.append(
                    f"{label} is priced off the {tariff_label!r} tariff, which "
                    f"is not the route named. Load a rate for this leg, or "
                    f"rename it to the tariff it is actually charged at."
                )
            cost = amount * multiplier(
                basis, pax=group.pax, nights=0, days=0, rooms=0, units=segment.units
            )
            charge = TransportCharge(
                sequence=segment.sequence,
                kind=segment.kind,
                mode=segment.mode,
                label=segment.description or tariff_label or label,
                basis=basis,
                units=segment.units,
                unit_amount=amount,
                currency=currency,
                cost=cost,
                is_optional=segment.is_optional,
                is_vvip=segment.is_vvip,
                source=source,
            )
            if segment.is_optional:
                out.optional.append(charge)
                optional_currency[currency] = (
                    optional_currency.get(currency, Decimal(0)) + cost
                )
                continue
            out.charges.append(charge)
            per_currency[currency] = per_currency.get(currency, Decimal(0)) + cost
            # Already multiplied out, so it travels as a group total — the same
            # shape as an accommodation subtotal. Nobody's residency or age
            # changes a seat's price, so the line names neither and is split
            # per head: a seat costs the same whoever is in it.
            out.lines.append(
                CostLine(
                    label="transport",
                    amount=cost,
                    currency=currency,
                    basis="per_group",
                )
            )

        out.total = await self._to_presentation(quote, per_currency)
        out.optional_total = await self._to_presentation(quote, optional_currency)
        # Optional movements are on the worksheet too, marked as what they are:
        # they are a real cost the moment the client accepts the upgrade, and
        # an operator reconciling an invoice needs the line either way.
        out.entries = [
            CostEntry(
                label=(
                    f"{charge.label} (optional)" if charge.is_optional
                    else charge.label
                ),
                # Optional movements are a component of their own on the
                # worksheet. Filed under "transport" they would sit above a
                # subtotal that deliberately excludes them, and a ledger whose
                # lines do not add up to its own subtotal is worse than no
                # ledger.
                component=(
                    "transport_optional" if charge.is_optional else "transport"
                ),
                basis=charge.basis,
                unit_amount=charge.unit_amount,
                # The multiplier that was actually applied — tickets for a
                # per-person fare, vehicles or legs for a group one — derived
                # the same way the charge was, not re-guessed from the total.
                quantity=multiplier(
                    charge.basis,
                    pax=group.pax,
                    nights=0,
                    days=0,
                    rooms=0,
                    units=charge.units,
                ),
                currency=charge.currency,
                extended=charge.cost,
                source=charge.source,
            )
            for charge in out.charges + out.optional
        ]
        return out

    async def _tariff_for(
        self, segment: QuoteTransportSegment, *, on: date
    ) -> tuple[Decimal, str, str, str, str] | None:
        """One movement's tariff: ``(amount, currency, basis, label, source)``.

        VAT-normalised here rather than at write time. Every other rate in the
        system is normalised at ingestion (:mod:`app.core.vat`), but these two
        tables are entered directly and carry the flag, so the gross-up has to
        happen on the way out — once, in one place, which is what keeps it from
        being a rule five call sites remember.
        """
        if segment.destination_id is None:
            return None

        if segment.kind == transport_rules.TRANSFER:
            rows = list(
                (
                    await self.db.execute(
                        select(TransferRate).where(
                            TransferRate.destination_id == segment.destination_id,
                            TransferRate.vehicle_type == (segment.vehicle_type or ""),
                            TransferRate.is_active.is_(True),
                            TransferRate.effective_from <= on,
                        )
                    )
                )
                .scalars()
                .all()
            )
            live = [r for r in rows if r.effective_to is None or r.effective_to >= on]
            if not live:
                return None
            # A route label narrows the tariff — "Nairobi CBD → SGR terminal" is
            # not the same drive as "terminus to hotel" — so an exact match wins
            # over the destination's general rate, which wins over another
            # named route we were not asked for.
            wanted = (segment.description or "").strip().casefold()
            best = max(
                live,
                key=lambda r: (
                    2 if wanted and r.route_label.strip().casefold() == wanted else
                    1 if not r.route_label.strip() else 0,
                    r.effective_from,
                ),
            )
            return (
                to_vat_inclusive(
                    best.price_per_leg,
                    vat_inclusive=best.vat_inclusive,
                    vat_pct=best.vat_pct,
                ),
                best.currency.upper(),
                transport_rules.line_basis("per_leg"),
                best.route_label,
                f"transfer_rates {best.id} · {best.vehicle_type} from "
                f"{best.effective_from}",
            )

        fares = list(
            (
                await self.db.execute(
                    select(DestinationTransportMode).where(
                        DestinationTransportMode.destination_id
                        == segment.destination_id,
                        DestinationTransportMode.mode == segment.mode,
                        DestinationTransportMode.travel_class
                        == (segment.travel_class or ""),
                        DestinationTransportMode.is_active.is_(True),
                        DestinationTransportMode.effective_from <= on,
                    )
                )
            )
            .scalars()
            .all()
        )
        current = [f for f in fares if f.effective_to is None or f.effective_to >= on]
        if not current:
            return None
        fare = max(current, key=lambda f: f.effective_from)
        return (
            to_vat_inclusive(
                fare.price, vat_inclusive=fare.vat_inclusive, vat_pct=fare.vat_pct
            ),
            fare.currency.upper(),
            transport_rules.line_basis(fare.cost_basis),
            fare.label or "",
            f"destination_transport_modes {fare.id} · {fare.mode}"
            + (f" {fare.travel_class}" if fare.travel_class else "")
            + f" from {fare.effective_from}",
        )

    async def _supplements(
        self,
        quote: Quote,
        accommodation_id: uuid.UUID,
        *,
        room_type_id: uuid.UUID,
        meal_plan_id: uuid.UUID,
        nights: list[date],
        pax: int,
        rooms: int,
    ) -> list[SupplementCharge]:
        """The mandatory supplements this stay runs into (§3.5a).

        Gathered after the room type is chosen rather than before, because
        "cheapest within the hotel" is decided on the room rate (§3.7) and the
        sheets state supplements for the whole property, not per room.
        """
        stmt = select(AccommodationSupplement).where(
            AccommodationSupplement.accommodation_id == accommodation_id,
            AccommodationSupplement.is_active.is_(True),
            AccommodationSupplement.is_mandatory.is_(True),
            AccommodationSupplement.effective_from <= nights[-1],
            AccommodationSupplement.effective_to >= nights[0],
        )
        out: list[SupplementCharge] = []
        for row in (await self.db.execute(stmt)).scalars().all():
            # NULL on any of the three scopes means "applies regardless".
            if row.room_type_id is not None and row.room_type_id != room_type_id:
                continue
            if row.meal_plan_id is not None and row.meal_plan_id != meal_plan_id:
                continue
            if (
                row.residence_category_id is not None
                and row.residence_category_id != quote.residence_category_id
            ):
                continue
            affected = sum(
                1 for night in nights if row.effective_from <= night <= row.effective_to
            )
            cost = supplement_cost(
                amount=row.amount,
                basis=row.basis,
                pax=pax,
                rooms=rooms,
                nights=affected,
            )
            if cost == 0:
                continue
            out.append(
                SupplementCharge(
                    label=row.label,
                    kind=row.kind,
                    basis=row.basis,
                    amount=row.amount,
                    currency=row.currency.upper(),
                    nights=affected,
                    units=multiplier(
                        row.basis,
                        pax=pax,
                        nights=affected,
                        days=affected,
                        rooms=rooms,
                    ),
                    source=(
                        f"accommodation_supplements {row.id} · {row.kind} "
                        f"from {row.effective_from}"
                    ),
                    cost=await self.fx.convert(
                        cost,
                        row.currency.upper(),
                        quote.presentation_currency.upper(),
                        nights[0],
                    ),
                )
            )
        return out

    async def _to_presentation(
        self, quote: Quote, amounts: dict[str, Decimal]
    ) -> Decimal:
        """Sum per-currency subtotals into the quote's presentation currency.

        Converted once per currency rather than once per night, so a ten-night
        stay costs one FX lookup instead of ten identical ones.
        """
        target = quote.presentation_currency.upper()
        total = Decimal(0)
        for currency, amount in amounts.items():
            total += await self.fx.convert(amount, currency, target, quote.arrival_date)
        return total

    async def _load(self, quote_id: uuid.UUID) -> Quote:
        quote = (
            await self.db.execute(select(Quote).where(Quote.id == quote_id))
        ).scalar_one_or_none()
        if quote is None:
            raise NotFoundError("Quote not found.")
        return quote
