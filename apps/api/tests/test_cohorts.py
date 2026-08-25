"""Cohort pricing: the group vector, cost bases and the per-cohort build-up.

No database. This is the arithmetic that decides what each traveller is charged,
so it is tested directly, and every worked figure below is computed by hand in
the comment above it.

The three properties worth stating up front, because most of this file exists to
defend them:

1. **Nobody is lost and nobody is invented.** Cohort counts sum to the headcount,
   attributed costs sum to the cost, and every guest gets a bed.
2. **Rooms split by residency, charges split by residency and traveller type.**
   Getting the second partition applied to the first would put a family of four
   in four rooms.
3. **The parts sum to the whole.** Each cohort's rounded per-person figure times
   its headcount is that cohort's total, and the cohort totals are the group
   total. A document whose halves disagree is the failure mode this design was
   built to remove.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import product

import pytest

from app.modules.quotes.cohorts import (
    BASES,
    Cohort,
    CostLine,
    Group,
    attribute,
    group_from_counts,
    multiplier,
    price_group,
)

D = Decimal

KES = "KES"
USD = "USD"
CURRENCIES = {"citizen": KES, "non_resident": USD}
CONTRACT_RATE = D("130")


def _convert(amount: Decimal, source: str, target: str) -> Decimal:
    """The seeded contract rate: USD 1 = KES 130 (§3.1b)."""
    if source == target:
        return amount
    if (source, target) == (USD, KES):
        return amount * CONTRACT_RATE
    if (source, target) == (KES, USD):
        return amount / CONTRACT_RATE
    raise AssertionError(f"no test rate for {source}->{target}")


def _rate(source: str, target: str) -> Decimal:
    return D(1) if source == target else CONTRACT_RATE


def _group(*counts: tuple[str, str, int]) -> Group:
    return group_from_counts(counts, CURRENCIES)


def _all_resident(pax: int) -> Group:
    return _group(("citizen", "adult", pax))


# --------------------------------------------------------------------------- #
# Cost bases
# --------------------------------------------------------------------------- #


def test_every_basis_has_a_multiplier():
    """A basis that parses but does not resolve would silently cost nothing."""
    for basis in BASES:
        assert (
            multiplier(basis, pax=10, nights=3, days=3, rooms=5) >= 1
        ), basis


def test_the_basis_changes_the_answer_completely():
    args = {"pax": 25, "nights": 3, "days": 3, "rooms": 13}
    assert multiplier("per_person_per_night", **args) == 75
    assert multiplier("per_person", **args) == 25
    assert multiplier("per_room_per_night", **args) == 39
    assert multiplier("per_room", **args) == 13
    assert multiplier("per_group_per_night", **args) == 3
    assert multiplier("per_group", **args) == 1


def test_nights_and_days_are_not_the_same_count():
    """A day excursion is one day of park fees and zero nights of accommodation.

    Conflating them either loses a day of fees or invents a night of a hotel.
    """
    day_trip = {"pax": 4, "nights": 0, "days": 1, "rooms": 0}
    assert multiplier("per_person_per_day", **day_trip) == 4
    assert multiplier("per_person_per_night", **day_trip) == 0


def test_units_scale_a_basis():
    """Two vehicles for the group, not one."""
    assert multiplier("per_group", pax=25, nights=3, days=3, rooms=13, units=2) == 2


def test_an_unknown_basis_is_refused_rather_than_assumed():
    with pytest.raises(ValueError, match="unknown cost basis"):
        multiplier("per_fortnight", pax=1, nights=1, days=1, rooms=1)


# --------------------------------------------------------------------------- #
# The group
# --------------------------------------------------------------------------- #


def test_a_headcount_is_the_sum_of_its_cohorts():
    group = _group(
        ("citizen", "adult", 18),
        ("citizen", "child", 2),
        ("non_resident", "adult", 5),
    )
    assert group.pax == 25
    assert group.headcount("citizen") == 20
    assert group.headcount("non_resident") == 5


def test_empty_boxes_on_a_form_are_not_an_error():
    """A form offering adults/children/infants submits zeros for the blanks."""
    group = _group(
        ("citizen", "adult", 4),
        ("citizen", "child", 0),
        ("citizen", "infant", 0),
    )
    assert group.pax == 4
    assert len(group.cohorts) == 1


def test_a_group_with_nobody_in_it_is_refused():
    with pytest.raises(ValueError, match="at least one traveller"):
        _group(("citizen", "adult", 0))


def test_the_same_cohort_twice_is_refused():
    with pytest.raises(ValueError, match="duplicate cohort"):
        Group(
            cohorts=(
                Cohort("citizen", "adult", 5, KES),
                Cohort("citizen", "adult", 3, KES),
            )
        )


def test_one_residency_cannot_be_billed_in_two_currencies():
    """A resident adult and a resident child are on the same sheet."""
    with pytest.raises(ValueError, match="disagree on currency"):
        Group(
            cohorts=(
                Cohort("citizen", "adult", 5, KES),
                Cohort("citizen", "child", 2, USD),
            )
        )


def test_a_single_cohort_group_is_uniform():
    assert _all_resident(25).is_uniform is True
    assert _group(("citizen", "adult", 24), ("citizen", "child", 1)).is_uniform is False


# --------------------------------------------------------------------------- #
# Rooming: by residency only
# --------------------------------------------------------------------------- #


def test_children_share_their_parents_room():
    """The partition that matters. Two adults and two children in a 4-guest villa
    are ONE room; splitting rooms by traveller type would make it four."""
    group = _group(("citizen", "adult", 2), ("citizen", "child", 2))
    assert group.rooming(capacity=4) == {"citizen": [4]}
    assert group.total_rooms(capacity=4) == 1


def test_a_family_in_twins_is_two_rooms_not_four():
    group = _group(("citizen", "adult", 2), ("citizen", "child", 2))
    assert group.total_rooms(capacity=2) == 2


def test_residencies_are_roomed_separately():
    """A resident and a non-resident cannot share a room whose rate is quoted per
    room at one residency — the room's price would be undefined."""
    group = _group(("citizen", "adult", 3), ("non_resident", "adult", 3))
    assert group.rooming(capacity=2) == {"citizen": [2, 1], "non_resident": [2, 1]}
    assert group.total_rooms(capacity=2) == 4


def test_the_extra_room_is_the_price_of_mixed_residency():
    """Six people in twins is three rooms as one group and four when split.

    Named explicitly because it is a real money difference that the obvious
    examples (25 people, 7 people) hide — both give 13 and 4 either way.
    """
    mixed = _group(("citizen", "adult", 3), ("non_resident", "adult", 3))
    single = _all_resident(6)
    assert mixed.total_rooms(capacity=2) == 4
    assert single.total_rooms(capacity=2) == 3


@pytest.mark.parametrize(
    "residents,non_residents",
    [(a, b) for a, b in product(range(0, 21), range(0, 21)) if a + b > 0],
)
def test_everyone_gets_a_bed_whatever_the_split(residents, non_residents):
    counts = []
    if residents:
        counts.append(("citizen", "adult", residents))
    if non_residents:
        counts.append(("non_resident", "adult", non_residents))
    group = group_from_counts(counts, CURRENCIES)
    plan = group.rooming(capacity=2)
    assert sum(sum(rooms) for rooms in plan.values()) == group.pax
    assert all(0 < occupants <= 2 for rooms in plan.values() for occupants in rooms)
    # Splitting by residency never needs fewer rooms than treating the group as
    # one, and never more than one extra per residency.
    whole = -(-group.pax // 2)
    assert whole <= group.total_rooms(capacity=2) <= whole + len(group.residences)


def test_twenty_five_residents_is_still_thirteen_rooms():
    """The single-residency case is untouched by any of this."""
    assert _all_resident(25).total_rooms(capacity=2) == 13
    assert _all_resident(7).total_rooms(capacity=2) == 4


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #


def test_a_shared_cost_is_split_per_head():
    """A seat on a coach costs the same whoever is in it."""
    group = _group(("citizen", "adult", 15), ("non_resident", "adult", 0))
    lines = [CostLine("coach", D("100000"), KES, "per_group")]
    attributed = attribute(lines, group, capacity=2)
    assert attributed["citizen:adult"]["coach"] == D("100000")


def test_a_shared_cost_across_cohorts_sums_exactly():
    """The parts must sum to the whole, even when the division does not terminate.

    100,000 across 3 residents and 4 residents-children is 42,857.142857...
    per group of three. Allocating by exact division and accepting the drift
    would leave the cohort totals adding up to something other than 100,000.
    """
    group = _group(("citizen", "adult", 3), ("citizen", "child", 4))
    attributed = attribute(
        [CostLine("chef", D("100000"), KES, "per_group")], group, capacity=2
    )
    total = sum(bucket["chef"] for bucket in attributed.values())
    assert total == D("100000")


def test_a_residence_scoped_line_reaches_only_that_residency():
    group = _group(("citizen", "adult", 10), ("non_resident", "adult", 5))
    lines = [
        CostLine("accommodation", D("343500"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("2400"), USD, "per_group", residence="non_resident"),
    ]
    attributed = attribute(lines, group, capacity=2)
    assert attributed["citizen:adult"]["accommodation"] == D("343500")
    assert attributed["non_resident:adult"]["accommodation"] == D("2400")


def test_a_child_line_reaches_only_the_children():
    group = _group(("citizen", "adult", 10), ("citizen", "child", 4))
    lines = [
        CostLine(
            "park fees", D("500"), KES, "per_person_per_day",
            residence="citizen", traveller_type="child", days=3,
        ),
        CostLine(
            "park fees", D("1000"), KES, "per_person_per_day",
            residence="citizen", traveller_type="adult", days=3,
        ),
    ]
    attributed = attribute(lines, group, capacity=2)
    # 4 children x 500 x 3 days, 10 adults x 1000 x 3 days
    assert attributed["citizen:child"]["park fees"] == D("6000")
    assert attributed["citizen:adult"]["park fees"] == D("30000")


def test_a_child_rate_on_an_all_adult_group_is_dropped_not_reassigned():
    """Charging somebody else's rate to whoever is left is the wrong answer."""
    group = _all_resident(10)
    lines = [
        CostLine(
            "child fee", D("500"), KES, "per_person",
            residence="citizen", traveller_type="child",
        )
    ]
    attributed = attribute(lines, group, capacity=2)
    assert attributed["citizen:adult"] == {}


def test_a_line_in_the_wrong_currency_with_no_converter_is_refused():
    """Dropping it would under-quote the booking by the whole line."""
    group = _all_resident(10)
    lines = [CostLine("boat", D("400"), USD, "per_group")]
    with pytest.raises(ValueError, match="no converter was given"):
        attribute(lines, group, capacity=2)


def test_a_shared_cost_on_a_mixed_currency_group_converts_per_share():
    """A coach chartered for a mixed group: one amount, two currencies out.

    KES 65,000 across 10 residents and 3 non-residents:
        residents      65,000 x 10/13 = 50,000  KES
        non-residents  the 15,000 remainder, converted at 130 = USD 115.3846...
    """
    group = _group(("citizen", "adult", 10), ("non_resident", "adult", 3))
    lines = [CostLine("coach", D("65000"), KES, "per_group")]
    attributed = attribute(lines, group, capacity=2, convert=_convert)
    assert attributed["citizen:adult"]["coach"] == D("50000")
    non_resident = attributed["non_resident:adult"]["coach"]
    assert non_resident == D("15000") / CONTRACT_RATE


def test_a_converted_share_round_trips_to_the_original_total():
    """Nothing is lost in the split beyond the rate's own precision.

    Exact equality cannot survive a round trip through a non-terminating rate —
    15,000/130 is 115.384615..., so this asserts the drift is sub-cent rather
    than pretending it is zero.
    """
    group = _group(("citizen", "adult", 10), ("non_resident", "adult", 3))
    attributed = attribute(
        [CostLine("coach", D("65000"), KES, "per_group")],
        group, capacity=2, convert=_convert,
    )
    back = (
        attributed["citizen:adult"]["coach"]
        + attributed["non_resident:adult"]["coach"] * CONTRACT_RATE
    )
    assert abs(back - D("65000")) < D("0.01")


def test_the_share_is_split_before_it_is_converted():
    """Splitting a converted total instead would give each cohort its own
    rounding of the exchange rate, so two runs could disagree by a cent."""
    group = _group(("citizen", "adult", 1), ("non_resident", "adult", 2))
    attributed = attribute(
        [CostLine("boat", D("300"), KES, "per_group")],
        group, capacity=2, convert=_convert,
    )
    # 300 split 1:2 is 100 KES and 200 KES, the latter converted — not 300
    # converted and then split.
    assert attributed["citizen:adult"]["boat"] == D("100")
    assert attributed["non_resident:adult"]["boat"] == D("200") / CONTRACT_RATE


def test_a_traveller_type_without_a_residence_is_not_expressible():
    with pytest.raises(ValueError, match="names a traveller type without a residence"):
        CostLine("all children", D("500"), KES, "per_person", traveller_type="child")


def test_a_negative_cost_line_is_refused():
    with pytest.raises(ValueError, match="negative"):
        CostLine("discount", D("-500"), KES, "per_group")


def test_room_based_lines_use_that_residency_rooms():
    """Three residents need two twins, so a per-room-per-night levy is charged
    twice a night, not once and a half."""
    group = _group(("citizen", "adult", 3), ("non_resident", "adult", 3))
    lines = [
        CostLine(
            "levy", D("200"), KES, "per_room_per_night",
            residence="citizen", nights=2,
        )
    ]
    attributed = attribute(lines, group, capacity=2)
    assert attributed["citizen:adult"]["levy"] == D("800")


def test_two_lines_with_one_label_accumulate():
    """Two park gates on one trip are both park fees."""
    group = _all_resident(10)
    lines = [
        CostLine("park fees", D("1000"), KES, "per_person", residence="citizen"),
        CostLine("park fees", D("500"), KES, "per_person", residence="citizen"),
    ]
    attributed = attribute(lines, group, capacity=2)
    assert attributed["citizen:adult"]["park fees"] == D("15000")


# --------------------------------------------------------------------------- #
# The build-up, per cohort
# --------------------------------------------------------------------------- #


def _price(group, lines, **over):
    kwargs = {
        "lines": lines,
        "group": group,
        "capacity": 2,
        "contingency_pct": D("5"),
        "profit_pct": D("24"),
        "group_currency": KES,
        "convert": _convert,
        "rate_used": _rate,
    }
    kwargs.update(over)
    return price_group(**kwargs)


def test_a_single_residency_group_prices_exactly_as_before():
    """The Stage 3 figure, unchanged, so this rework does not move existing prices.

    Coral Sands, 25 residents, three nights, accommodation 343,500:
        + contingency 5%   17,175  -> cost_basis 360,675
        + profit 24%       86,562  -> 447,237
        per person  ceil(447,237 / 25 / 100) x 100 = 17,900
        group       17,900 x 25                    = 447,500
    """
    group = _all_resident(25)
    lines = [
        CostLine("accommodation", D("343500"), KES, "per_group", residence="citizen")
    ]
    priced = _price(group, lines)
    assert len(priced.cohorts) == 1
    assert priced.per_person("citizen") == D("17900")
    assert priced.group_total == D("447500")


def test_each_cohort_gets_its_own_per_person_figure():
    """Residents in KES, non-residents in USD, from the same booking.

    Residents: 20 people, 10 twins at 9,000 x 3 nights = 270,000
        + 5% = 283,500, + 24% = 351,540
        per person ceil(351,540 / 20 / 100) x 100 = 17,600 -> 352,000
    Non-residents: 5 people, 3 twins at USD 180 x 3 nights = USD 1,620
        + 5% = 1,701, + 24% = 2,109.24
        per person ceil(2,109.24 / 5 / 100) x 100 = 500 -> USD 2,500
    """
    group = _group(("citizen", "adult", 20), ("non_resident", "adult", 5))
    lines = [
        CostLine("accommodation", D("270000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("1620"), USD, "per_group", residence="non_resident"),
    ]
    priced = _price(group, lines)
    assert priced.per_person("citizen") == D("17600")
    assert priced.per_person("non_resident") == D("500")
    # One booking, one total: 352,000 + (2,500 x 130)
    assert priced.group_total == D("352000") + D("325000")


def test_the_conversion_rate_is_disclosed():
    """A converted total with an unstated rate is a dispute waiting to happen."""
    group = _group(("citizen", "adult", 10), ("non_resident", "adult", 2))
    lines = [
        CostLine("accommodation", D("90000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("700"), USD, "per_group", residence="non_resident"),
    ]
    priced = _price(group, lines)
    assert priced.conversions == {"USD/KES": CONTRACT_RATE}


def test_a_single_currency_group_needs_no_converter():
    group = _all_resident(10)
    lines = [CostLine("accommodation", D("90000"), KES, "per_group", residence="citizen")]
    priced = _price(group, lines, convert=None, rate_used=None)
    assert priced.group_total > 0
    assert priced.conversions == {}


def test_a_missing_converter_is_an_error_not_a_silent_omission():
    """Dropping the cohort we cannot convert would under-quote the booking."""
    group = _group(("citizen", "adult", 10), ("non_resident", "adult", 2))
    lines = [
        CostLine("accommodation", D("90000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("700"), USD, "per_group", residence="non_resident"),
    ]
    with pytest.raises(ValueError, match="no converter"):
        _price(group, lines, convert=None)


@pytest.mark.parametrize(
    "composition",
    [
        (("citizen", "adult", 25),),
        (("citizen", "adult", 24), ("citizen", "child", 1)),
        (("citizen", "adult", 20), ("non_resident", "adult", 5)),
        (("citizen", "adult", 12), ("citizen", "child", 3), ("non_resident", "adult", 10)),
        (("non_resident", "adult", 2), ("non_resident", "infant", 1)),
        (("citizen", "adult", 1),),
    ],
)
def test_every_cohort_reconciles_with_its_own_total(composition):
    """The property the whole design exists for: per-person x headcount is the
    cohort total, for every cohort, at every composition."""
    group = group_from_counts(composition, CURRENCIES)
    lines = [
        CostLine("accommodation", D("400000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("3000"), USD, "per_group", residence="non_resident"),
    ]
    # A shared line has one currency, so it is only expressible on a group that
    # bills in one. A mixed-residency group scopes its costs per residency
    # instead — which is the constraint, not a limitation of the test.
    if len(group.residences) == 1:
        currency = group.currency_for(group.residences[0])
        lines.append(
            CostLine(
                "boat", D("60000") if currency == KES else D("500"),
                currency, "per_group",
            )
        )
    priced = _price(group, lines)
    for price in priced.cohorts:
        assert price.per_person * price.cohort.count == price.total, price.cohort.key


@pytest.mark.parametrize(
    "composition",
    [
        (("citizen", "adult", 25),),
        (("citizen", "adult", 24), ("citizen", "child", 1)),
        (("citizen", "adult", 10), ("citizen", "child", 5), ("citizen", "infant", 2)),
    ],
)
def test_the_group_total_is_the_sum_of_the_cohort_totals(composition):
    group = group_from_counts(composition, CURRENCIES)
    lines = [
        CostLine("accommodation", D("343500"), KES, "per_group", residence="citizen"),
        CostLine("chef", D("30000"), KES, "per_group"),
    ]
    priced = _price(group, lines)
    assert priced.group_total == sum(price.total for price in priced.cohorts)


def test_the_group_total_never_undercharges_after_rounding():
    """Rounding each cohort's per-person figure up can only raise the total.

    The bound is one rounding step **per traveller**, not per cohort: rounding a
    per-person figure up by 99 and then multiplying by ten heads adds 990 to that
    cohort. Which means per-person rounding is a real source of margin on a large
    group — a 25-person booking can carry up to 2,500 of it — and also the reason
    a group total is 447,500 against a 447,237 cost. Worth knowing rather than
    discovering in a reconciliation.
    """
    group = _group(("citizen", "adult", 7), ("citizen", "child", 3))
    lines = [
        CostLine("accommodation", D("123456"), KES, "per_group", residence="citizen")
    ]
    priced = _price(group, lines)
    raw = sum(price.build_up.selling_total for price in priced.cohorts)
    assert priced.group_total >= raw
    assert priced.group_total - raw < D("100") * group.pax


def test_the_agent_cover_fee_is_split_per_head_and_never_marked_up():
    """25,000 across 20 residents and 5 non-residents is 20,000 and 5,000, and
    neither share picks up the 24%."""
    group = _group(("citizen", "adult", 20), ("non_resident", "adult", 5))
    lines = [
        CostLine("accommodation", D("270000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("1620"), USD, "per_group", residence="non_resident"),
    ]
    with_fee = _price(group, lines, agent_cover_fee=D("25000"))
    shares = {
        price.cohort.key: price.build_up.agent_cover_fee
        for price in with_fee.cohorts
    }
    assert shares["citizen:adult"] == D("20000")
    assert shares["non_resident:adult"] == D("5000")
    assert sum(shares.values()) == D("25000")
    for price in with_fee.cohorts:
        # after_profit + fee == selling_total, so the fee sits outside the markup
        assert (
            price.build_up.after_profit + price.build_up.agent_cover_fee
            == price.build_up.selling_total
        )


def test_a_child_priced_at_a_child_rate_pays_less_than_an_adult():
    """Child rates have been stored since Stage 2 and used nowhere. This is the
    first arithmetic that actually charges one."""
    group = _group(("citizen", "adult", 2), ("citizen", "child", 2))
    lines = [
        # One villa for the family, plus fees that differ by traveller type.
        CostLine("accommodation", D("48000"), KES, "per_group", residence="citizen"),
        CostLine(
            "park fees", D("1000"), KES, "per_person_per_day",
            residence="citizen", traveller_type="adult", days=3,
        ),
        CostLine(
            "park fees", D("500"), KES, "per_person_per_day",
            residence="citizen", traveller_type="child", days=3,
        ),
    ]
    priced = _price(group, lines, capacity=4)
    adult = priced.per_person("citizen", "adult")
    child = priced.per_person("citizen", "child")
    assert child < adult


def test_every_figure_stays_decimal():
    """A float anywhere here would round money, which §2 forbids."""
    group = _group(("citizen", "adult", 3), ("non_resident", "adult", 2))
    lines = [
        CostLine("accommodation", D("33333"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("777"), USD, "per_group", residence="non_resident"),
    ]
    priced = _price(group, lines)
    assert isinstance(priced.group_total, Decimal)
    for price in priced.cohorts:
        assert isinstance(price.per_person, Decimal)
        assert isinstance(price.total, Decimal)


def test_attribution_loses_nothing():
    """Everything charged reaches exactly one cohort — the invariant that stops a
    cost being dropped or counted twice."""
    group = _group(
        ("citizen", "adult", 12),
        ("citizen", "child", 3),
        ("non_resident", "adult", 10),
    )
    lines = [
        CostLine("accommodation", D("400000"), KES, "per_group", residence="citizen"),
        CostLine("accommodation", D("3000"), USD, "per_group", residence="non_resident"),
        CostLine(
            "park fees", D("1000"), KES, "per_person_per_day",
            residence="citizen", traveller_type="adult", days=2,
        ),
    ]
    attributed = attribute(lines, group, capacity=2)
    kes = sum(
        amount
        for key, bucket in attributed.items()
        if key.startswith("citizen")
        for amount in bucket.values()
    )
    usd = sum(
        amount
        for key, bucket in attributed.items()
        if key.startswith("non_resident")
        for amount in bucket.values()
    )
    assert kes == D("400000") + D("24000")
    assert usd == D("3000")
