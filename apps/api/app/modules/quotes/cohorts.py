"""Group composition, cost bases and the per-cohort build-up (design doc §3.6b).

Stage 3 priced a quote as one undifferentiated headcount at one residency. Real
groups are not that: residents and non-residents are charged different rates in
different currencies, and adults and children are charged differently again. This
module models the group as **cohorts** and every cost as an **(amount, currency,
basis)** triple resolved against them.

Three ideas carry the whole design.

**A cost line is meaningless without its basis.** The same 3,300 is a wildly
different number per person per night than per room, and supplier sheets use
both. So a basis travels with every amount and the multiplier is derived from the
group — never hand-computed at the call site. This generalises what
:func:`app.modules.quotes.options.supplement_cost` already did for four bases.

**Rooming cohorts and charging cohorts are not the same partition.** This is the
easy thing to get wrong and it is expensive in both directions:

* Rooms are split **by residency only**. A room is priced per room at one
  residency, so a resident and a non-resident cannot share one without making the
  room's rate undefined.
* Charges are split **by residency and traveller type**, because a child pays a
  child rate — but a child still sleeps in their parents' room. Partitioning rooms
  by traveller type too would put a family of two adults and two children in four
  rooms instead of one.

**Per-person is derived, and the group total is derived from it.** Costs are
built on the whole group (thirteen rooms, one vehicle, fees summed), attributed to
cohorts, and only then divided. Each cohort's rounded per-person figure is
multiplied back out, and the group total is the sum of those — so every figure on
the document reconciles with every other. Dividing a rounded group total instead
is what makes the reference proposal contradict itself (§3.6).

All money is ``Decimal``; every amount arriving here is already VAT-inclusive
(§3.2), so nothing in this module adds tax.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.modules.quotes.options import BuildUp, build_up, room_plan, rooms_required

# --------------------------------------------------------------------------- #
# Cost bases
# --------------------------------------------------------------------------- #

# Every basis a supplier sheet or a fee table actually uses. Adding one here is
# the only place a new charging shape needs to be taught, which is the point of
# doing this as a table rather than as arithmetic at each call site.
#
# ``per_group`` covers a figure already totalled for the group it belongs to —
# an accommodation subtotal, a chartered vehicle, a chef's fee. Accommodation
# arrives pre-totalled on purpose: rate selection across occupancies, seasons and
# room types is the pricing service's job, and re-deriving it here would mean two
# implementations of the same rule.
BASES: frozenset[str] = frozenset(
    {
        "per_person_per_night",
        "per_person_per_day",
        "per_person",
        "per_room_per_night",
        "per_room",
        "per_group_per_night",
        "per_group_per_day",
        "per_group",
    }
)


def multiplier(
    basis: str, *, pax: int, nights: int, days: int, rooms: int, units: int = 1
) -> int:
    """How many times an amount is charged, given its basis.

    ``nights`` and ``days`` are deliberately separate. A stay is counted in
    nights — a guest checking in on the 20th and out on the 23rd sleeps there
    three times — while park and conservation fees are charged per 24-hour period
    of presence, which for an overnight leg is the same count but for a day
    excursion is one against zero nights. Conflating them either loses a day of
    park fees or invents a night of accommodation.
    """
    if basis not in BASES:
        raise ValueError(f"unknown cost basis: {basis!r}")
    if units < 0:
        raise ValueError("units cannot be negative")
    per = {
        "per_person_per_night": pax * nights,
        "per_person_per_day": pax * days,
        "per_person": pax,
        "per_room_per_night": rooms * nights,
        "per_room": rooms,
        "per_group_per_night": nights,
        # KWS charges a vehicle by seat band per *day* in the park — 4,500 for a
        # 25-44 seater — which is a group charge on a day count, and was the one
        # real basis this table did not have. ``units`` carries the vehicle count.
        "per_group_per_day": days,
        "per_group": 1,
    }[basis]
    return max(0, per) * units


# --------------------------------------------------------------------------- #
# Group discounts on park entry (KWS "FEES FOR GROUP ACTIVITIES")
# --------------------------------------------------------------------------- #

# KWS publishes a MICE ladder for pre-booked groups. Its wording — "Amount of
# fees: 30% of the applicable park entry fees" — is genuinely ambiguous: read
# literally the group *pays* 30%, but then a 10-29 group paying 5% would get a
# far better deal than a 100+ group paying 30%, which inverts a volume ladder.
# The only reading where the ladder is monotonic is a **discount** percentage,
# so that is what is modelled.
#
# It is shipped switched OFF (see ``mice_discount_pct`` returning 0 unless a
# ladder is supplied) because the two readings differ by an order of magnitude
# and the safe error is the visible one: failing to claim a discount we are owed
# shows up as a slightly high quote, while applying a 95% reduction we are not
# owed shows up as a loss nobody notices. Enable it in the pricing config once
# KWS confirms the reading.
MICE_LADDER: tuple[tuple[int, int, Decimal], ...] = (
    (10, 29, Decimal("5")),
    (30, 49, Decimal("10")),
    (50, 99, Decimal("20")),
    (100, 10_000_000, Decimal("30")),
)


def mice_discount_pct(
    pax: int, *, ladder: Sequence[tuple[int, int, Decimal]] | None = None
) -> Decimal:
    """The pre-booked-group discount on park entry fees for ``pax`` people.

    Returns zero when no ladder is configured, so the discount is opt-in rather
    than a silent default.
    """
    if not ladder:
        return Decimal(0)
    for low, high, pct in ladder:
        if low <= pax <= high:
            return pct
    return Decimal(0)


# --------------------------------------------------------------------------- #
# The group
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cohort:
    """One set of travellers who all pay the same price in the same currency.

    ``residence`` is a residence-category key (``citizen``, ``non_resident``);
    ``traveller_type`` is ``adult`` / ``child`` / ``infant`` as
    :func:`app.modules.park_fees.service.classify_age` assigns it. The pair is
    the cohort's identity, so a resident child and a resident adult are different
    cohorts while two resident adults are not.
    """

    residence: str
    traveller_type: str
    count: int
    currency: str

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("a cohort with no travellers is not a cohort")
        if not self.residence or not self.traveller_type:
            raise ValueError("a cohort needs a residence and a traveller type")
        if len(self.currency) != 3:
            raise ValueError("currency must be a three-letter code")

    @property
    def key(self) -> str:
        return f"{self.residence}:{self.traveller_type}"


@dataclass(frozen=True)
class Group:
    """The travellers on a quote, partitioned two ways.

    Built from cohort *counts* rather than from named traveller rows, because
    that is how a group booking is actually quoted: "twenty-five people, six of
    them non-resident, two children". Named travellers remain available for
    passport-level detail at booking time; they are not what pricing needs.
    """

    cohorts: tuple[Cohort, ...]

    def __post_init__(self) -> None:
        if not self.cohorts:
            raise ValueError("a quote needs at least one traveller")
        keys = [cohort.key for cohort in self.cohorts]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate cohort in group: {sorted(keys)}")
        # One currency per residency, not per cohort: a resident adult and a
        # resident child are billed on the same sheet, so quoting them in
        # different currencies would be a data error rather than a choice.
        for residence in self.residences:
            currencies = {
                cohort.currency for cohort in self.cohorts
                if cohort.residence == residence
            }
            if len(currencies) > 1:
                raise ValueError(
                    f"{residence} cohorts disagree on currency: {sorted(currencies)}"
                )

    @property
    def pax(self) -> int:
        return sum(cohort.count for cohort in self.cohorts)

    @property
    def residences(self) -> tuple[str, ...]:
        """Residencies present, in first-seen order so output is stable."""
        seen: list[str] = []
        for cohort in self.cohorts:
            if cohort.residence not in seen:
                seen.append(cohort.residence)
        return tuple(seen)

    @property
    def is_uniform(self) -> bool:
        """Whether one per-person figure describes the whole group."""
        return len(self.cohorts) == 1

    def currency_for(self, residence: str) -> str:
        for cohort in self.cohorts:
            if cohort.residence == residence:
                return cohort.currency
        raise KeyError(residence)

    def headcount(self, residence: str) -> int:
        return sum(
            cohort.count for cohort in self.cohorts if cohort.residence == residence
        )

    def cohorts_in(self, residence: str) -> tuple[Cohort, ...]:
        return tuple(
            cohort for cohort in self.cohorts if cohort.residence == residence
        )

    # -- the two partitions ------------------------------------------------- #

    def rooming(self, capacity: int) -> dict[str, list[int]]:
        """Rooms per residency, as the number of guests sleeping in each.

        Split by residency **only** — see the module docstring. The cost of this
        rule is the occasional extra room: three residents and three
        non-residents need four rooms in twins, not three, because no room can
        hold one of each and still have a defined rate. That is the price of
        mixed-residency groups being quotable at all.
        """
        return {
            residence: room_plan(self.headcount(residence), capacity)
            for residence in self.residences
        }

    def rooms_for(self, residence: str, capacity: int) -> int:
        return rooms_required(self.headcount(residence), capacity)

    def total_rooms(self, capacity: int) -> int:
        return sum(
            self.rooms_for(residence, capacity) for residence in self.residences
        )


def group_from_counts(
    counts: Iterable[tuple[str, str, int]], currencies: dict[str, str]
) -> Group:
    """Build a group from ``(residence, traveller_type, count)`` triples.

    Zero counts are dropped rather than rejected: a form that offers boxes for
    adults, children and infants will submit zeros for the ones nobody filled
    in, and that is not an error.
    """
    cohorts = tuple(
        Cohort(
            residence=residence,
            traveller_type=traveller_type,
            count=count,
            currency=currencies[residence],
        )
        for residence, traveller_type, count in counts
        if count > 0
    )
    return Group(cohorts=cohorts)


# --------------------------------------------------------------------------- #
# Cost lines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CostLine:
    """One cost, with everything needed to know who bears it and how often.

    ``residence``/``traveller_type`` say who the line belongs to. Both ``None``
    means the cost is **shared** — a chartered coach, a chef, a boat hired for the
    group — and is split per head, because a seat costs the same whoever is in it.
    A residence with no traveller type is shared within that residency, which is
    what an accommodation subtotal is.
    """

    label: str
    amount: Decimal
    currency: str
    basis: str
    residence: str | None = None
    traveller_type: str | None = None
    nights: int = 0
    days: int = 0
    rooms: int = 0
    units: int = 1

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"unknown cost basis: {self.basis!r}")
        if self.amount < 0:
            raise ValueError(f"cost line {self.label!r} is negative")
        if self.traveller_type is not None and self.residence is None:
            # A line for "all children regardless of residency" would have to be
            # priced at two different rates at once, so it is not expressible.
            raise ValueError(
                f"cost line {self.label!r} names a traveller type without a "
                "residence; a child rate belongs to one residency's sheet"
            )

    def cost(self, *, pax: int, rooms: int) -> Decimal:
        return self.amount * multiplier(
            self.basis,
            pax=pax,
            nights=self.nights,
            days=self.days,
            rooms=rooms,
            units=self.units,
        )


def _split_per_head(
    total: Decimal, cohorts: Sequence[Cohort]
) -> dict[str, Decimal]:
    """Divide a shared cost across cohorts by headcount, summing exactly.

    The last cohort takes the remainder. Allocating by exact division and
    accepting the drift would leave the cohort totals adding up to something
    other than the shared cost, and a document whose parts do not sum to its
    whole is the specific failure this whole design exists to avoid. Which cohort
    absorbs it is deterministic rather than arbitrary, so re-pricing the same
    quote cannot move a shilling between cohorts.
    """
    if not cohorts:
        return {}
    heads = sum(cohort.count for cohort in cohorts)
    shares: dict[str, Decimal] = {}
    running = Decimal(0)
    for cohort in cohorts[:-1]:
        share = (total * cohort.count / heads).quantize(Decimal("0.0001"))
        shares[cohort.key] = share
        running += share
    shares[cohorts[-1].key] = total - running
    return shares


def attribute(
    lines: Iterable[CostLine],
    group: Group,
    *,
    capacity: int,
    convert: Callable[[Decimal, str, str], Decimal] | None = None,
) -> dict[str, dict[str, Decimal]]:
    """Resolve every line and attribute it to cohorts.

    Returns ``{cohort_key: {label: amount}}`` — the component dict each cohort's
    build-up needs, **in that cohort's own currency**, since mixing currencies
    inside one build-up would make the arithmetic meaningless.

    A **shared** cost is the case that needs ``convert``. A coach chartered for a
    mixed group is one amount in one currency whose per-head share has to land in
    two: the residents' share in shillings, the non-residents' in dollars. So the
    share is computed first, in the line's own currency, and only then converted —
    that order matters, because splitting a converted total instead would let each
    cohort's share carry its own rounding of the exchange rate.

    Without a converter, a line whose currency does not match its cohorts is an
    error rather than a silent omission: dropping it would under-quote the
    booking by the whole line.
    """
    per_cohort: dict[str, dict[str, Decimal]] = {
        cohort.key: {} for cohort in group.cohorts
    }

    for line in lines:
        if line.residence is not None and line.traveller_type is not None:
            targets = [
                cohort for cohort in group.cohorts
                if cohort.residence == line.residence
                and cohort.traveller_type == line.traveller_type
            ]
        elif line.residence is not None:
            targets = list(group.cohorts_in(line.residence))
        else:
            targets = list(group.cohorts)

        if not targets:
            # A line for a cohort nobody is in — a child rate on an all-adult
            # group. Silently dropping it is right: the alternative is charging
            # somebody else's rate to whoever is left.
            continue

        expected = {cohort.currency for cohort in targets}
        if expected != {line.currency} and convert is None:
            raise ValueError(
                f"cost line {line.label!r} is in {line.currency} but its cohorts "
                f"are billed in {sorted(expected)}, and no converter was given"
            )

        if line.residence is not None:
            pax = sum(cohort.count for cohort in targets)
            rooms = group.rooms_for(line.residence, capacity)
        else:
            pax = group.pax
            rooms = group.total_rooms(capacity)

        total = line.cost(pax=pax, rooms=rooms)
        if total == 0:
            continue

        if len(targets) == 1:
            shares = {targets[0].key: total}
        else:
            shares = _split_per_head(total, targets)

        billed = {cohort.key: cohort.currency for cohort in targets}
        for key, share in shares.items():
            if billed[key] != line.currency:
                assert convert is not None  # guarded above
                share = convert(share, line.currency, billed[key])
            bucket = per_cohort[key]
            bucket[line.label] = bucket.get(line.label, Decimal(0)) + share

    return per_cohort


# --------------------------------------------------------------------------- #
# The build-up, per cohort
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CohortPrice:
    """What one cohort pays, and everything behind it."""

    cohort: Cohort
    build_up: BuildUp

    @property
    def per_person(self) -> Decimal:
        # Never None: a cohort is uniform by construction, which is what makes a
        # per-person figure meaningful for it even when the group is mixed.
        assert self.build_up.per_person is not None
        return self.build_up.per_person

    @property
    def total(self) -> Decimal:
        return self.build_up.group_total

    @property
    def currency(self) -> str:
        return self.cohort.currency


@dataclass(frozen=True)
class GroupPrice:
    """Every cohort's price, plus one figure for the whole booking."""

    cohorts: tuple[CohortPrice, ...]
    group_total: Decimal
    group_currency: str
    # The rates used to reach ``group_total``, for disclosure. A converted total
    # with an unstated rate is a dispute waiting to happen.
    conversions: dict[str, Decimal] = field(default_factory=dict)

    @property
    def pax(self) -> int:
        return sum(price.cohort.count for price in self.cohorts)

    def per_person(self, residence: str, traveller_type: str = "adult") -> Decimal:
        for price in self.cohorts:
            if (
                price.cohort.residence == residence
                and price.cohort.traveller_type == traveller_type
            ):
                return price.per_person
        raise KeyError(f"{residence}:{traveller_type}")


def price_group(
    *,
    lines: Iterable[CostLine],
    group: Group,
    capacity: int,
    contingency_pct: Decimal,
    profit_pct: Decimal,
    agent_cover_fee: Decimal = Decimal(0),
    rounding_step: Decimal = Decimal("100"),
    group_currency: str,
    convert: Callable[[Decimal, str, str], Decimal] | None = None,
    rate_used: Callable[[str, str], Decimal] | None = None,
) -> GroupPrice:
    """Price a group cohort by cohort, then sum.

    The agent cover fee is a per-quote group figure, so it is split per head like
    any other shared cost — and, as in §3.6, it is added *after* profit so it
    reaches the client at face value.

    ``convert`` turns a cohort total into ``group_currency``. It is injected
    rather than imported so this module stays pure and the exchange rate a
    quotation was priced at can be pinned to the quote rather than read live at
    render time. Omitting it is only valid when every cohort already bills in
    ``group_currency``.
    """
    components = attribute(lines, group, capacity=capacity, convert=convert)
    fee_shares = _split_per_head(agent_cover_fee, group.cohorts) if agent_cover_fee else {}

    priced: list[CohortPrice] = []
    for cohort in group.cohorts:
        priced.append(
            CohortPrice(
                cohort=cohort,
                build_up=build_up(
                    components=components[cohort.key],
                    pax=cohort.count,
                    contingency_pct=contingency_pct,
                    profit_pct=profit_pct,
                    agent_cover_fee=fee_shares.get(cohort.key, Decimal(0)),
                    rounding_step=rounding_step,
                    uniform_group=True,
                ),
            )
        )

    total = Decimal(0)
    conversions: dict[str, Decimal] = {}
    for price in priced:
        if price.currency == group_currency:
            total += price.total
            continue
        if convert is None:
            raise ValueError(
                f"cohort billed in {price.currency} but no converter was given "
                f"to reach {group_currency}"
            )
        total += convert(price.total, price.currency, group_currency)
        if rate_used is not None:
            conversions[f"{price.currency}/{group_currency}"] = rate_used(
                price.currency, group_currency
            )

    return GroupPrice(
        cohorts=tuple(priced),
        group_total=total,
        group_currency=group_currency,
        conversions=conversions,
    )
