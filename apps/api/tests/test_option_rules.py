"""Stage 3.3 — the pure option-pricing rules, against the design doc's examples.

No database: this is the arithmetic that decides what a client is charged, so it
is tested directly and exhaustively. Where a figure appears in the design doc or
in a real supplier sheet, that figure is used rather than a made-up one.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from itertools import product

import pytest

from app.modules.quotes.options import (
    build_up,
    costed_rate,
    meal_plan_chain,
    meals_needing_chef,
    meets_minimum_stay,
    minimum_stay_reason,
    needs_chef,
    nights_within,
    rate_for_occupancy,
    resolve_meal_plan,
    retained_discount,
    room_plan,
    rooms_required,
    round_up_to,
    stay_nights,
    supplement_cost,
    supplier_paid,
    uniform_group,
)

D = Decimal


# --------------------------------------------------------------------------- #
# Rooming (§3.3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pax", "capacity", "rooms"),
    [
        (25, 2, 13),  # the reference quotation's group in twins
        (25, 4, 7),  # Pendo's 4-guest villas
        (24, 2, 12),  # exact fit, no odd room
        (1, 2, 1),
        (2, 2, 1),
        (3, 2, 2),
    ],
)
def test_rooms_required_is_ceiling_division(pax, capacity, rooms):
    assert rooms_required(pax, capacity) == rooms


def test_room_plan_puts_the_odd_guest_in_a_room_of_their_own():
    """25 guests in twins is twelve doubles and one single, not 12.5 rooms."""
    plan = room_plan(25, 2)
    assert plan == [2] * 12 + [1]
    assert sum(plan) == 25
    assert len(plan) == rooms_required(25, 2)


def test_room_plan_for_a_villa():
    plan = room_plan(25, 4)
    assert plan == [4] * 6 + [1]
    assert sum(plan) == 25 and len(plan) == 7


def test_the_odd_room_is_charged_at_the_suppliers_single_rate():
    """Temple Point Creek Deluxe FB: 28,400 single, 37,600 double.

    The odd room is charged in full at the single rate, which is neither half a
    double nor a whole one — the reason occupancy is part of rate identity.
    """
    single, double = D("28400"), D("37600")
    plan = room_plan(25, 2)
    rate_for = {1: single, 2: double}
    total = sum(rate_for[occupancy] for occupancy in plan)
    assert total == 12 * double + single == D("479600")
    # Not half the double, and not a second double either.
    assert total != 12 * double + double / 2
    assert total != 13 * double


@pytest.mark.parametrize(("pax", "capacity"), [(0, 2), (-1, 2), (5, 0)])
def test_rooming_refuses_nonsense(pax, capacity):
    with pytest.raises(ValueError):
        rooms_required(pax, capacity)


# --------------------------------------------------------------------------- #
# Meal plans (§3.4)
# --------------------------------------------------------------------------- #


def test_the_fallback_chain_is_full_board_then_half_then_bed_and_breakfast():
    assert meal_plan_chain("FB") == ("FB", "HB", "BB")


@pytest.mark.parametrize(
    ("requested", "available", "chosen", "is_fallback"),
    [
        ("FB", {"FB", "HB", "BB"}, "FB", False),
        ("FB", {"HB", "BB"}, "HB", True),
        # Kaskazi is bed-and-breakfast only: the chain ends there.
        ("FB", {"BB"}, "BB", True),
        ("HB", {"HB"}, "HB", False),
        ("HB", {"FB"}, "FB", True),
        ("FB", set(), None, False),
    ],
)
def test_resolve_meal_plan_reports_whether_it_fell_back(
    requested, available, chosen, is_fallback
):
    """The flag matters as much as the choice.

    An option priced on a different board basis is not comparable with the
    others, and the agent has to be told rather than left to notice.
    """
    assert resolve_meal_plan(requested, available) == (chosen, is_fallback)


@pytest.mark.parametrize(
    ("plan", "chef"),
    [("BB", True), ("RO", True), ("HB", False), ("FB", False), ("AI", False)],
)
def test_a_chef_is_only_needed_where_meals_are_not_included(plan, chef):
    """Never on a half-board or full-board option (§3.4)."""
    assert needs_chef(plan) is chef


# --------------------------------------------------------------------------- #
# Discounts and STO (§3.5)
# --------------------------------------------------------------------------- #


def test_a_rack_discount_is_halved_to_the_client_but_paid_in_full():
    """The design doc's worked example: 15% off a 24,000 rack rate.

    Three distinct numbers, which is why all three are tracked.
    """
    rate, pct = D("24000"), D("15")
    assert supplier_paid(rate, pct) == D("20400")  # what we pay the hotel
    assert costed_rate(rate, pct, "rack") == D("22200")  # enters the build-up
    assert retained_discount(rate, pct, "rack") == D("1800")  # kept as margin
    # The client's half and ours are the same size.
    assert D("24000") - D("22200") == D("22200") - D("20400")


def test_an_sto_rate_is_used_as_the_cost_directly():
    """STO sheets are already operator rates, so nothing is held back.

    The whole stated discount reduces the figure that enters the build-up, and
    there is no retained half.
    """
    rate, pct = D("24000"), D("10")
    assert supplier_paid(rate, pct) == D("21600")
    assert costed_rate(rate, pct, "sto") == D("21600")
    assert retained_discount(rate, pct, "sto") == D("0")


def test_a_rate_with_no_stated_discount_is_untouched():
    for kind in ("rack", "sto"):
        assert costed_rate(D("18000"), None, kind) == D("18000")
        assert costed_rate(D("18000"), D("0"), kind) == D("18000")
        assert retained_discount(D("18000"), None, kind) == D("0")


def test_retained_discount_scales_with_the_number_of_rooms():
    """Margin is per room-night, not per booking."""
    assert retained_discount(D("24000"), D("15"), "rack", units=13) == D("23400")


# --------------------------------------------------------------------------- #
# Supplements (§3.5a)
# --------------------------------------------------------------------------- #


def test_nights_inside_a_supplement_window():
    """Temple Point loads 24-25 December inside a stay running 20 Dec - 2 Jan."""
    stay_start, stay_end = date(2027, 12, 20), date(2028, 1, 2)
    assert nights_within(stay_start, stay_end, date(2027, 12, 24), date(2027, 12, 25)) == 2
    # A stay that ends before the window pays nothing.
    assert (
        nights_within(
            stay_start, date(2027, 12, 23), date(2027, 12, 24), date(2027, 12, 25)
        )
        == 0
    )
    # A stay wholly inside a long window is counted by its own nights.
    assert (
        nights_within(
            date(2027, 12, 24), date(2027, 12, 26), date(2027, 12, 1), date(2027, 12, 31)
        )
        == 2
    )


def test_the_christmas_supplement_from_the_real_sheet():
    """"Supplement Christmas: KSH 3300 per person per night (24.12 & 25.12)".

    Money a quote would otherwise silently omit.
    """
    assert (
        supplement_cost(
            amount=D("3300"), basis="per_person_per_night", pax=25, rooms=13, nights=2
        )
        == D("165000")
    )


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        ("per_person_per_night", D("165000")),
        ("per_person", D("82500")),
        ("per_room_per_night", D("85800")),
        ("per_room", D("42900")),
    ],
)
def test_the_basis_changes_the_answer_completely(basis, expected):
    """The same 3,300 is four different totals, which is why the basis is stored."""
    assert (
        supplement_cost(amount=D("3300"), basis=basis, pax=25, rooms=13, nights=2)
        == expected
    )


def test_a_supplement_outside_the_stay_costs_nothing():
    assert (
        supplement_cost(
            amount=D("3300"), basis="per_person_per_night", pax=25, rooms=13, nights=0
        )
        == D("0")
    )


def test_an_unknown_basis_is_refused_rather_than_assumed():
    with pytest.raises(ValueError):
        supplement_cost(amount=D("1"), basis="per_fortnight", pax=1, rooms=1, nights=1)


# --------------------------------------------------------------------------- #
# Minimum stay (§3.3a)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nights", "minimum", "ok"),
    [(4, 4, True), (5, 4, True), (3, 4, False), (1, None, True), (10, None, True)],
)
def test_minimum_stay(nights, minimum, ok):
    assert meets_minimum_stay(nights, minimum) is ok


def test_the_rejection_reason_is_safe_to_print():
    """It goes on the client's document verbatim, so it names no cost."""
    reason = minimum_stay_reason(3, 4)
    assert reason == (
        "Requires a minimum stay of 4 nights; this itinerary is 3 nights."
    )
    for forbidden in ("cost", "margin", "profit", "KES", "supplier"):
        assert forbidden.lower() not in reason.lower()
    assert minimum_stay_reason(1, 1).startswith("Requires a minimum stay of 1 night;")


# --------------------------------------------------------------------------- #
# The build-up (§3.6)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (D("28361.20"), D("100"), D("28400")),
        (D("28400"), D("100"), D("28400")),  # already on the step
        (D("28400.01"), D("100"), D("28500")),
        (D("1"), D("100"), D("100")),
    ],
)
def test_round_up_to(value, step, expected):
    assert round_up_to(value, step) == expected


def test_the_build_up_order_is_contingency_then_profit_then_the_agent_fee():
    """Both orderings are deliberate (§3.6).

    Contingency sits inside the cost basis so profit accrues on it; the agent
    cover fee is added after profit so it reaches the client at face value.
    """
    result = build_up(
        components={"accommodation": D("100000")},
        pax=10,
        contingency_pct=D("5"),
        profit_pct=D("24"),
        agent_cover_fee=D("5000"),
    )
    assert result.cost_subtotal == D("100000")
    assert result.contingency_value == D("5000")
    assert result.cost_basis == D("105000")
    # Profit is on the basis, contingency included: 105,000 x 24%.
    assert result.profit_value == D("25200")
    assert result.after_profit == D("130200")
    # The fee is added afterwards, so it is not marked up.
    assert result.selling_total == D("135200")
    assert result.per_person == D("13600")  # 13,520 rounded up to the next 100
    assert result.group_total == D("136000")


def test_the_agent_cover_fee_is_never_marked_up():
    """Adding 1,000 to the fee must add exactly 1,000 to the selling total."""
    common = {
        "components": {"accommodation": D("100000")},
        "pax": 10,
        "contingency_pct": D("5"),
        "profit_pct": D("24"),
    }
    without = build_up(**common, agent_cover_fee=D("0"))
    with_fee = build_up(**common, agent_cover_fee=D("1000"))
    assert with_fee.selling_total - without.selling_total == D("1000")


def test_contingency_does_accrue_profit():
    """The alternative ordering would be cheaper, so this is worth pinning."""
    with_contingency = build_up(
        components={"a": D("100000")},
        pax=1,
        contingency_pct=D("5"),
        profit_pct=D("24"),
    )
    # If contingency were added after profit it would be 124,000 + 5,000.
    assert with_contingency.after_profit == D("130200")
    assert with_contingency.after_profit != D("129000")


def test_per_person_is_computed_first_so_the_two_headline_numbers_agree():
    """The rule that makes the sample quotation's contradiction impossible.

    That document says 28,800 per person on one page and 28,400 in a table, both
    against a 720,000 group total. Rounding the per-person figure up and then
    multiplying guarantees the two agree by construction.
    """
    result = build_up(
        components={"accommodation": D("500000")},
        pax=25,
        contingency_pct=D("5"),
        profit_pct=D("24"),
    )
    assert result.per_person is not None
    assert result.per_person * 25 == result.group_total
    assert result.group_total >= result.selling_total  # rounding only ever adds
    # And the per-person figure is a round hundred, as the document shows it.
    assert result.per_person % D("100") == 0


def test_the_group_total_never_undercharges_after_rounding():
    """Rounding up per person must not produce a total below the true cost."""
    for pax in range(1, 30):
        result = build_up(
            components={"a": D("123457")},
            pax=pax,
            contingency_pct=D("5"),
            profit_pct=D("24"),
        )
        assert result.group_total >= result.selling_total


def test_per_person_is_suppressed_for_a_group_that_is_not_uniform():
    """A mixed-residency or adult-plus-child group is quoted as a total (§3.6).

    A per-person figure there would be an average nobody is actually paying.
    """
    result = build_up(
        components={"a": D("100000")},
        pax=3,
        contingency_pct=D("5"),
        profit_pct=D("24"),
        uniform_group=False,
    )
    assert result.per_person is None
    assert result.group_total == round_up_to(result.selling_total, D("100"))


def test_every_figure_stays_decimal():
    """No float may touch money, per the repo conventions."""
    result = build_up(
        components={"a": D("100000"), "b": D("250.55")},
        pax=7,
        contingency_pct=D("5"),
        profit_pct=D("24"),
        agent_cover_fee=D("125.25"),
    )
    for name, value in vars(result).items():
        if name == "components":
            assert all(isinstance(v, Decimal) for v in value.values())
        elif value is not None:
            assert isinstance(value, Decimal), name


@pytest.mark.parametrize(
    "kwargs",
    [
        {"components": {"a": D("-1")}, "pax": 2},
        {"components": {"a": D("10")}, "pax": 0},
        {"components": {"a": D("10")}, "pax": 2, "agent_cover_fee": D("-5")},
    ],
)
def test_the_build_up_refuses_impossible_input(kwargs):
    kwargs.setdefault("contingency_pct", D("5"))
    kwargs.setdefault("profit_pct", D("24"))
    with pytest.raises(ValueError):
        build_up(**kwargs)


def test_zero_percentages_are_allowed_and_change_nothing():
    result = build_up(
        components={"a": D("1000")},
        pax=1,
        contingency_pct=D("0"),
        profit_pct=D("0"),
    )
    assert result.selling_total == D("1000")
    assert result.per_person == D("1000")


# --------------------------------------------------------------------------- #
# The stay, as nights
# --------------------------------------------------------------------------- #

def test_a_stay_is_counted_in_nights_not_days():
    nights = stay_nights(date(2026, 12, 20), date(2026, 12, 23))
    # Checks in on the 20th, out on the 23rd: three nights, and the 23rd is not
    # one of them.
    assert nights == [date(2026, 12, 20), date(2026, 12, 21), date(2026, 12, 22)]


def test_one_night_is_a_stay():
    assert stay_nights(date(2026, 7, 1), date(2026, 7, 2)) == [date(2026, 7, 1)]


def test_a_stay_across_a_year_boundary():
    nights = stay_nights(date(2026, 12, 31), date(2027, 1, 2))
    assert nights == [date(2026, 12, 31), date(2027, 1, 1)]


@pytest.mark.parametrize(
    "arrival,departure",
    [
        (date(2026, 7, 1), date(2026, 7, 1)),  # same day
        (date(2026, 7, 2), date(2026, 7, 1)),  # backwards
    ],
)
def test_a_stay_with_no_nights_is_refused(arrival, departure):
    with pytest.raises(ValueError):
        stay_nights(arrival, departure)


# --------------------------------------------------------------------------- #
# Per-occupancy rate selection (§3.3)
# --------------------------------------------------------------------------- #

# Temple Point 2027/28, Creek Deluxe, full board, high season — the real sheet.
TEMPLE_POINT = {1: D("28400"), 2: D("37600")}


def test_an_exact_occupancy_wins():
    assert rate_for_occupancy(TEMPLE_POINT, 1) == (1, D("28400"))
    assert rate_for_occupancy(TEMPLE_POINT, 2) == (2, D("37600"))


def test_a_single_is_neither_half_a_double_nor_a_whole_one():
    _, single = rate_for_occupancy(TEMPLE_POINT, 1)
    _, double = rate_for_occupancy(TEMPLE_POINT, 2)
    assert single != double / 2
    assert single != double


def test_an_unquoted_occupancy_takes_the_next_larger_room():
    """A lone guest at a hotel that only prices doubles pays for the double."""
    assert rate_for_occupancy({2: D("24000"), 3: D("30000")}, 1) == (2, D("24000"))


def test_more_guests_than_any_quoted_room_is_not_priced():
    """There is no honest way to put three guests in a room priced for two."""
    assert rate_for_occupancy({1: D("6500"), 2: D("9000")}, 3) is None


def test_no_rates_at_all_is_not_priced():
    assert rate_for_occupancy({}, 2) is None


def test_occupancy_must_be_positive():
    with pytest.raises(ValueError):
        rate_for_occupancy(TEMPLE_POINT, 0)


# --------------------------------------------------------------------------- #
# Chef meal counts (§3.4)
# --------------------------------------------------------------------------- #

def test_bed_and_breakfast_leaves_lunch_and_dinner():
    assert meals_needing_chef("BB", 3) == 6


def test_room_only_leaves_all_three_meals():
    assert meals_needing_chef("RO", 3) == 9


@pytest.mark.parametrize("plan", ["HB", "FB", "AI"])
def test_a_fed_plan_needs_no_chef_meals(plan):
    """Half board leaves lunch but never takes a chef, so it is zero, not one."""
    assert meals_needing_chef(plan, 5) == 0
    assert needs_chef(plan) is False


def test_meal_counts_scale_with_the_stay():
    assert meals_needing_chef("BB", 0) == 0
    assert meals_needing_chef("bb", 10) == 20


# --------------------------------------------------------------------------- #
# When a per-person figure is meaningful (§3.6, §3.6a)
# --------------------------------------------------------------------------- #

def test_a_headcount_only_group_is_uniform():
    """25 adults entered as pax_count, not as 25 traveller rows."""
    assert uniform_group([]) is True


def test_all_adults_is_uniform():
    assert uniform_group(["adult"] * 25) is True


def test_an_adult_and_a_child_is_not_uniform():
    assert uniform_group(["adult", "child"]) is False


# --------------------------------------------------------------------------- #
# Stage 3.7 sweeps — the invariants, rather than one example each
#
# The tests above assert the design doc's worked figures. These assert the
# properties those figures are instances of, across a range wide enough to
# include the edges: 0% and 100% discounts, percentages that do not divide
# cleanly, and every headcount-and-capacity pair up to a 40-person group.
# --------------------------------------------------------------------------- #

_PERCENTAGES = [None, D("0"), D("7.5"), D("10"), D("15"), D("33.333"), D("100")]


@pytest.mark.parametrize("pct", _PERCENTAGES)
def test_the_client_is_never_costed_below_what_the_hotel_is_paid(pct):
    rack = D("24000")
    paid = supplier_paid(rack, pct)
    costed = costed_rate(rack, pct, "rack")
    assert paid <= costed <= rack


@pytest.mark.parametrize("pct", _PERCENTAGES)
def test_the_retained_half_is_exactly_the_difference(pct):
    rack = D("24000")
    kept = retained_discount(rack, pct, "rack")
    assert kept == costed_rate(rack, pct, "rack") - supplier_paid(rack, pct)
    assert kept >= 0


@pytest.mark.parametrize("pct", _PERCENTAGES)
def test_the_client_gets_exactly_half_the_concession(pct):
    """Half, not "about half": the sheet's percentage is halved, not the money
    rounded to something convenient."""
    rack = D("24000")
    if pct is None or pct == 0:
        assert costed_rate(rack, pct, "rack") == rack
        return
    whole = rack - supplier_paid(rack, pct)
    passed_on = rack - costed_rate(rack, pct, "rack")
    assert passed_on * 2 == whole


@pytest.mark.parametrize("pct", _PERCENTAGES)
def test_an_sto_sheet_keeps_nothing_back(pct):
    """An STO rate is already an operator rate; halving it a second time would
    quote the client above a price that was never rack."""
    rack = D("24000")
    assert costed_rate(rack, pct, "sto") == supplier_paid(rack, pct)
    assert retained_discount(rack, pct, "sto") == 0


def test_a_hundred_percent_discount_costs_the_client_half_the_rack_rate():
    """Not a realistic sheet, but the arithmetic must not invert or go negative."""
    assert supplier_paid(D("24000"), D("100")) == 0
    assert costed_rate(D("24000"), D("100"), "rack") == D("12000")
    assert retained_discount(D("24000"), D("100"), "rack") == D("12000")


@pytest.mark.parametrize("pax,capacity", list(product(range(1, 41), range(1, 7))))
def test_every_guest_gets_a_bed_and_no_room_is_overfilled(pax, capacity):
    plan = room_plan(pax, capacity)
    assert sum(plan) == pax, "a guest was lost or invented"
    assert all(0 < occupants <= capacity for occupants in plan)
    assert len(plan) == rooms_required(pax, capacity) == math.ceil(pax / capacity)
    # At most one room is short: rooms are filled before another is opened.
    assert len([n for n in plan if n < capacity]) <= 1


def test_twenty_five_in_twins_is_twelve_doubles_and_a_single():
    assert rooms_required(25, 2) == 13
    assert room_plan(25, 2) == [2] * 12 + [1]


def test_twenty_five_in_four_guest_villas_is_seven_units():
    """The capacity-4 case. Six full villas and one holding the last guest."""
    assert rooms_required(25, 4) == 7
    assert room_plan(25, 4) == [4] * 6 + [1]


def test_an_exact_multiple_opens_no_extra_room():
    assert room_plan(24, 4) == [4] * 6
    assert rooms_required(24, 4) == 6


def test_a_lone_guest_takes_one_room_however_large():
    assert room_plan(1, 4) == [1]
    assert rooms_required(1, 4) == 1


def test_a_group_smaller_than_one_room_still_books_the_room():
    assert room_plan(3, 4) == [3]


def test_half_board_is_preferred_to_bed_and_breakfast():
    plan, fell_back = resolve_meal_plan("FB", {"HB", "BB"})
    assert (plan, fell_back) == ("HB", True)


def test_the_requested_plan_wins_when_it_exists():
    plan, fell_back = resolve_meal_plan("FB", {"FB", "HB", "BB"})
    assert (plan, fell_back) == ("FB", False)


def test_bed_and_breakfast_is_the_end_of_the_chain():
    plan, fell_back = resolve_meal_plan("FB", {"BB"})
    assert (plan, fell_back) == ("BB", True)


def test_nothing_available_is_not_a_silent_choice():
    assert resolve_meal_plan("FB", set()) == (None, False)


