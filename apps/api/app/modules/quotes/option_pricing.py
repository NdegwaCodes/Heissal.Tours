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
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
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
from app.modules.quotes.cohorts import Group
from app.modules.quotes.group import build_group, residence_ids
from app.modules.quotes.models import Quote, QuoteOption, QuoteRejectedCandidate
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

# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


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


@dataclass
class OptionCosting:
    """One priced option: everything behind it, plus the two client figures."""

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


# The one fee type an accommodation option implies. Conservancy, camping and the
# rest attach to an activity or a leg rather than to a bed, so they are not
# charged here — see the 3.9 packages work.
PARK_ENTRY = "park_entry"


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

        result = OptionPricingResult()
        for option in sorted(quote.options, key=lambda o: o.sort_order):
            await self._price_one(
                quote,
                option,
                nights=nights,
                group=group,
                requested_plan=requested_plan,
                contingency_pct=contingency,
                profit_pct=profit,
                rounding_step=cfg.per_person_rounding,
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

        by_accommodation = {c.accommodation_id: c for c in result.options}
        for option in quote.options:
            costing = by_accommodation.get(option.accommodation_id)
            if costing is None:
                continue
            option.room_type_id = costing.room_type_id
            option.meal_plan_id = costing.meal_plan_id
            option.rooms_required = costing.rooms_required
            option.meal_plan_fallback_from = costing.meal_plan_fallback_from
            option.is_comparable = costing.is_comparable

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
        rounding_step: Decimal,
        into: OptionPricingResult,
    ) -> None:
        pax = group.pax
        accommodation = await self.db.get(Accommodation, option.accommodation_id)
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
            return

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
            return
        plan_code, is_fallback = resolve_meal_plan(requested_plan, available)
        if plan_code is None:
            into.warnings.append(
                f"{accommodation.name}: no rate on any plan in the "
                f"{requested_plan} fallback chain, so it could not be priced."
            )
            return
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
                into.warnings.append(
                    f"{accommodation.name}: no room type could house {pax} guests "
                    f"at the rates on file, so it could not be priced."
                )
            return

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
        park_total, park_warnings = await self._park_fees(
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

        totals = build_up(
            components=components,
            pax=pax,
            contingency_pct=contingency_pct,
            profit_pct=profit_pct,
            agent_cover_fee=option.agent_cover_fee,
            rounding_step=rounding_step,
            # A per-person figure is only meaningful when everyone pays the same.
            # The vector is what finally makes residency part of that judgement:
            # before it, only traveller type could vary, so a mixed-residency
            # group got one per-person figure covering two currencies.
            uniform_group=group.is_uniform,
        )
        into.options.append(
            OptionCosting(
                accommodation_id=accommodation.id,
                accommodation_name=accommodation.name,
                room_type_id=best.room_type_id,
                room_type_name=best.room_type_name,
                meal_plan_id=plan.id,
                meal_plan_code=plan_code,
                meal_plan_name=plan.name,
                meal_plan_fallback_from=requested_plan if is_fallback else None,
                rooms_required=best.rooms,
                nights=len(nights),
                currency=quote.presentation_currency.upper(),
                components=components,
                supplements=supplements,
                supplier_paid_total=best.paid_total,
                retained_discount=best.retained_discount,
                build_up=totals,
                # An option priced on a different board basis is not comparable
                # with the others; an agent flag can only narrow that, not widen it.
                is_comparable=option.is_comparable and not is_fallback,
                warnings=warnings,
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
        derived: set[int] = set()
        per_person_basis: set[str] = set()
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
                    costed[currency] = costed.get(currency, Decimal(0)) + costed_rate(
                        amount, rate.supplier_discount_pct, rate.rate_kind
                    )

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

    async def _park_fees(
        self,
        quote: Quote,
        accommodation: Accommodation,
        *,
        nights: list[date],
        group: Group,
    ) -> tuple[Decimal, list[str]]:
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
            return Decimal(0), []

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

        per_currency: dict[str, Decimal] = {}
        uncovered: set[str] = set()
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
                per_currency[currency] = per_currency.get(
                    currency, Decimal(0)
                ) + amount * cohort.count

        warnings: list[str] = []
        if uncovered:
            warnings.append(
                f"{accommodation.name}: this destination charges park fees but "
                f"none is on file for {', '.join(sorted(uncovered))} across the "
                f"whole stay, so those travellers are quoted without them. Load "
                f"the missing schedule before issuing."
            )
        return await self._to_presentation(quote, per_currency), warnings

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
