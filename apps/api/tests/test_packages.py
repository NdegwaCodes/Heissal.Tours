"""Stage 3.9 — the leg rules for a multi-destination package, as pure functions.

No database: these are the rules that decide whether a quote can be issued, and
they are cheap enough to test exhaustively over dates rather than through HTTP.

The scenario throughout is the client's own: **2 or 3 destinations in a single
7–30 day trip**. Arrival 1 July, departure 8 July — seven nights — split
Nairobi 1 night, Maasai Mara 3, Diani 3.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.modules.quotes import packages as P
from app.modules.quotes.packages import Leg

ARRIVAL, DEPARTURE = date(2026, 7, 1), date(2026, 7, 8)


def leg(sequence, destination, check_in, check_out, name=""):
    return Leg(
        sequence=sequence,
        destination=destination,
        check_in=date(2026, 7, check_in),
        check_out=date(2026, 7, check_out),
        property_name=name,
    )


def valid():
    """Nairobi 1 night, Mara 3, Diani 3 — a contiguous seven-night trip."""
    return [
        leg(1, "Nairobi", 1, 2, "Soames Hotel"),
        leg(2, "Maasai Mara", 2, 5, "Kazuri Mara Camp"),
        leg(3, "Diani", 5, 8, "Swahili Beach Resort"),
    ]


def codes(problems):
    return {problem.code for problem in problems}


def _check(legs, **over):
    kwargs = {"arrival": ARRIVAL, "departure": DEPARTURE}
    kwargs.update(over)
    return P.check(legs, **kwargs)


# --------------------------------------------------------------------------- #
# The shape of a correct package
# --------------------------------------------------------------------------- #


def test_a_contiguous_package_has_no_problems():
    assert _check(valid()) == []


def test_the_legs_nights_add_up_to_the_stay():
    """Seven nights, split 1 + 3 + 3. If the parts do not sum to the whole then
    something is either unpaid for or paid for twice."""
    legs = valid()
    assert [entry.nights for entry in P.order(legs)] == [1, 3, 3]
    assert P.total_nights(legs) == (DEPARTURE - ARRIVAL).days == 7


def test_check_out_is_the_next_leg_check_in():
    """The date a guest changes hotel belongs to both legs and is a night for
    neither twice: they sleep at the first up to that morning and the second
    from that afternoon."""
    first, second, third = P.order(valid())
    assert first.check_out == second.check_in
    assert second.check_out == third.check_in


def test_a_legs_nights_exclude_its_check_out_date():
    """A 5–8 July leg is the nights of the 5th, 6th and 7th. Including the
    check-out date would charge a night nobody sleeps."""
    nights = P.nights_of(leg(3, "Diani", 5, 8))
    assert nights == [date(2026, 7, 5), date(2026, 7, 6), date(2026, 7, 7)]
    assert date(2026, 7, 8) not in nights


def test_order_is_by_sequence_and_never_by_date():
    """Sorting by date would silently repair a mis-sequenced package into one
    that looks valid, hiding the mistake instead of reporting it."""
    legs = valid()
    scrambled = [legs[2], legs[0], legs[1]]
    assert [entry.sequence for entry in P.order(scrambled)] == [1, 2, 3]

    # Sequence 1 given the last dates: the fault must survive ordering.
    wrong = [
        leg(1, "Diani", 5, 8),
        leg(2, "Nairobi", 1, 2),
        leg(3, "Maasai Mara", 2, 5),
    ]
    assert P.order(wrong)[0].destination == "Diani"
    assert codes(_check(wrong)) & {P.OVERLAP, P.EARLY_START, P.LONG_END}


# --------------------------------------------------------------------------- #
# Contiguity — the faults a finished document cannot show
# --------------------------------------------------------------------------- #


def test_a_gap_between_legs_is_blocking():
    """A night with no bed. The client arrives at hotel two a day late, or sleeps
    somewhere nobody paid for — and the per-person figure looks entirely normal,
    which is why this cannot be a warning."""
    legs = [
        leg(1, "Nairobi", 1, 2),
        leg(2, "Maasai Mara", 3, 5),  # a night missing on 2 July
        leg(3, "Diani", 5, 8),
    ]
    problems = _check(legs)
    assert P.GAP in codes(problems)
    assert P.blocking(problems)
    gap = next(p for p in problems if p.code == P.GAP)
    assert "1 night(s)" in gap.message
    assert gap.sequence == 2


def test_an_overlap_between_legs_is_blocking():
    """A night paid for twice, in two towns."""
    legs = [
        leg(1, "Nairobi", 1, 3),
        leg(2, "Maasai Mara", 2, 5),  # starts before Nairobi ends
        leg(3, "Diani", 5, 8),
    ]
    problems = _check(legs)
    assert P.OVERLAP in codes(problems)
    overlap = next(p for p in problems if p.code == P.OVERLAP)
    assert "1 night(s)" in overlap.message


@pytest.mark.parametrize("size", [1, 2, 5])
def test_a_gap_reports_how_many_nights_are_missing(size):
    legs = [leg(1, "Nairobi", 1, 2), leg(2, "Diani", 2 + size, 8)]
    gap = next(p for p in _check(legs) if p.code == P.GAP)
    assert f"{size} night(s)" in gap.message


def test_a_leg_that_checks_out_before_it_checks_in_is_caught():
    legs = [leg(1, "Nairobi", 5, 2)]
    assert P.BAD_RANGE in codes(_check(legs))


def test_a_zero_night_leg_is_not_a_leg():
    """Same date in and out. It would contribute no nights and no cost while
    looking like a stop on the itinerary."""
    legs = [leg(1, "Nairobi", 1, 1), leg(2, "Diani", 1, 8)]
    assert P.BAD_RANGE in codes(_check(legs))


# --------------------------------------------------------------------------- #
# The package has to cover exactly the trip that was sold
# --------------------------------------------------------------------------- #


def test_a_package_starting_after_arrival_is_blocking():
    legs = [leg(1, "Nairobi", 2, 3), leg(2, "Diani", 3, 8)]
    problems = _check(legs)
    assert P.LATE_START in codes(problems)
    assert "1 night(s) unaccounted" in next(
        p for p in problems if p.code == P.LATE_START
    ).message


def test_a_package_starting_before_arrival_is_blocking():
    legs = [leg(1, "Nairobi", 1, 2)]
    assert P.EARLY_START in codes(
        _check(legs, arrival=date(2026, 7, 2), departure=date(2026, 7, 2))
    )


def test_a_package_ending_before_departure_is_blocking():
    """The commonest version of this: an agent drops the last leg's nights by one
    while editing and the client is homeless on their final night."""
    legs = [
        leg(1, "Nairobi", 1, 2),
        leg(2, "Maasai Mara", 2, 5),
        leg(3, "Diani", 5, 7),
    ]
    problems = _check(legs)
    assert P.SHORT_END in codes(problems)
    assert "1 night(s) unaccounted" in next(
        p for p in problems if p.code == P.SHORT_END
    ).message


def test_a_package_running_past_departure_is_blocking():
    legs = [leg(1, "Nairobi", 1, 2), leg(2, "Diani", 2, 9)]
    assert P.LONG_END in codes(_check(legs))


def test_an_empty_package_cannot_be_priced():
    problems = _check([])
    assert codes(problems) == {P.NO_LEGS}
    assert P.blocking(problems)


# --------------------------------------------------------------------------- #
# Minimum stay, which behaves differently for a package
# --------------------------------------------------------------------------- #


def test_a_leg_below_its_minimum_stay_blocks_the_whole_package():
    """For a single property, a minimum stay the itinerary cannot meet drops it
    from the comparison and says so on the document (§3.3a). For a package it
    cannot: the package is ONE offer, so a leg that cannot be booked makes the
    whole thing unbookable rather than shorter.
    """
    legs = valid()
    problems = _check(legs, minimum_stay={1: 2})  # Nairobi has only 1 night
    assert P.TOO_SHORT in codes(problems)
    assert P.blocking(problems)
    assert "requires 2" in next(
        p for p in problems if p.code == P.TOO_SHORT
    ).message


def test_a_leg_meeting_its_minimum_stay_passes():
    assert _check(valid(), minimum_stay={2: 3, 3: 3}) == []


def test_a_minimum_stay_for_a_leg_that_does_not_exist_is_ignored():
    assert _check(valid(), minimum_stay={9: 40}) == []


# --------------------------------------------------------------------------- #
# Reported, not refused
# --------------------------------------------------------------------------- #


def test_two_legs_at_one_position_leave_the_order_undefined():
    legs = [
        leg(1, "Nairobi", 1, 2),
        leg(1, "Maasai Mara", 2, 5),
        leg(3, "Diani", 5, 8),
    ]
    assert P.DUPLICATE_SEQUENCE in codes(_check(legs))


def test_a_repeated_destination_is_a_note_not_a_fault():
    """Nairobi at both ends of a safari is the commonest itinerary in Kenya, so
    this cannot block. It is still surfaced, because the other reason a
    destination repeats is a copied leg nobody re-pointed."""
    legs = [
        leg(1, "Nairobi", 1, 2),
        leg(2, "Maasai Mara", 2, 7),
        leg(3, "Nairobi", 7, 8),
    ]
    problems = _check(legs)
    assert codes(problems) == {P.REPEATED_DESTINATION}
    assert P.blocking(problems) == []
    assert "check it is not a copied leg" in problems[0].message


# --------------------------------------------------------------------------- #
# The range the client actually asked about
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nights", [7, 14, 21, 30])
def test_a_trip_of_any_length_the_client_named_validates(nights):
    """"2 or 3 destinations in a single 7-30 day trip" — split as evenly as the
    nights allow, with the remainder on the last leg."""
    arrival = date(2026, 7, 1)
    departure = arrival + timedelta(days=nights)
    each = nights // 3
    first = arrival + timedelta(days=each)
    second = first + timedelta(days=each)
    legs = [
        Leg(1, "Nairobi", arrival, first),
        Leg(2, "Maasai Mara", first, second),
        Leg(3, "Diani", second, departure),
    ]
    assert P.check(legs, arrival=arrival, departure=departure) == []
    assert P.total_nights(legs) == nights


def test_a_single_leg_package_is_still_a_valid_package():
    """The old single-property option, expressed as a package of one. It has to
    keep working or every existing quote becomes invalid."""
    legs = [Leg(1, "Diani", ARRIVAL, DEPARTURE, "Swahili Beach Resort")]
    assert P.check(legs, arrival=ARRIVAL, departure=DEPARTURE) == []
    assert P.total_nights(legs) == 7
