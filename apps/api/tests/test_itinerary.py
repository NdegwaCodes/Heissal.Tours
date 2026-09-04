"""The day-by-day programme (Stage 4.1). No database.

Two things are being defended here, and only the first is what a reader expects
of an itinerary.

**The shape of a trip.** A booking has one more day than it has nights, and the
last of them is the one with the flight home on it. Every off-by-one in this
module loses either the departure day or the last night, and both are the kind
of error a document renders perfectly plausibly.

**The days an agent typed are days of this trip.** Excursion fares are selected
by day number (§3.8) and transfer tariffs by travel date (§3.10), so a day nine
on a five-day trip is not a presentation problem — it is a fare picked from a
date the client is not in the country. Nothing checked that until now.
"""

from __future__ import annotations

from datetime import date

from app.modules.quotes.itinerary import (
    ACTIVITY_OFF_TRIP,
    ACTIVITY_UNSCHEDULED,
    MOVEMENT_OFF_TRIP,
    MOVEMENT_UNDATED,
    NO_DAYS,
    Excursion,
    Movement,
    build,
    check,
    dates,
    programme,
)
from app.modules.quotes.packages import Leg

# A four-day trip: three nights, 1 to 4 July.
ARRIVAL, DEPARTURE = date(2026, 7, 1), date(2026, 7, 4)


def _leg(sequence, destination, check_in, check_out, *, board="FB", property_name=""):
    return Leg(
        sequence=sequence,
        destination=destination,
        check_in=check_in,
        check_out=check_out,
        property_name=property_name or f"{destination} Hotel",
        board=board,
    )


def _single():
    return [_leg(1, "Diani", ARRIVAL, DEPARTURE)]


def _package():
    """Nairobi for one night, then the Mara for two — a real shape of trip."""
    return [
        _leg(1, "Nairobi", ARRIVAL, date(2026, 7, 2), board="BB"),
        _leg(2, "Maasai Mara", date(2026, 7, 2), DEPARTURE, board="FB"),
    ]


# --------------------------------------------------------------------------- #
# The shape of a trip
# --------------------------------------------------------------------------- #


def test_a_trip_has_one_more_day_than_it_has_nights():
    """Three nights, four days. The fourth is the one they fly home on."""
    assert len(dates(arrival=ARRIVAL, departure=DEPARTURE)) == 4
    days = build(_single(), arrival=ARRIVAL, departure=DEPARTURE)
    assert [day.number for day in days] == [1, 2, 3, 4]
    assert [day.on for day in days] == [
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
        date(2026, 7, 4),
    ]
    assert sum(1 for day in days if day.has_night) == 3


def test_the_first_and_last_days_are_marked():
    days = build(_single(), arrival=ARRIVAL, departure=DEPARTURE)
    assert days[0].is_arrival and not days[0].is_departure
    assert days[-1].is_departure and not days[-1].is_arrival
    assert not any(day.is_arrival or day.is_departure for day in days[1:-1])


def test_a_one_night_trip_is_two_days():
    """The shortest real booking, and where an off-by-one shows first."""
    days = build(
        [_leg(1, "Nairobi", ARRIVAL, date(2026, 7, 2))],
        arrival=ARRIVAL,
        departure=date(2026, 7, 2),
    )
    assert len(days) == 2
    assert days[0].has_night and not days[1].has_night


def test_a_departure_before_arrival_has_no_days_and_says_so():
    assert dates(arrival=DEPARTURE, departure=ARRIVAL) == []
    assert build(_single(), arrival=DEPARTURE, departure=ARRIVAL) == []
    problems = check(arrival=DEPARTURE, departure=ARRIVAL)
    assert [p.code for p in problems] == [NO_DAYS]
    assert problems[0].blocking


def test_a_same_day_trip_is_one_day():
    """Nobody sleeps, so nobody is charged a night — but the day exists.

    A day excursion is a real product and the difference between nights and
    days is exactly what makes it expressible (§3.6b).
    """
    days = build([], arrival=ARRIVAL, departure=ARRIVAL)
    assert len(days) == 1
    assert days[0].is_arrival and days[0].is_departure


# --------------------------------------------------------------------------- #
# Which leg holds which day
# --------------------------------------------------------------------------- #


def test_a_day_belongs_to_the_leg_that_holds_its_night():
    """The Nairobi night is day one; the Mara nights are days two and three.

    The 2nd of July is both legs' date — checkout from one, check-in to the
    other (§3.9) — and it belongs to the leg the client *sleeps* under, which
    is the Mara.
    """
    days = build(_package(), arrival=ARRIVAL, departure=DEPARTURE)
    assert [day.destination for day in days] == [
        "Nairobi",
        "Maasai Mara",
        "Maasai Mara",
        "Maasai Mara",
    ]
    assert [day.leg for day in days] == [1, 2, 2, 2]


def test_the_departure_day_shows_the_property_they_woke_up_in():
    """It has no night of its own, which is what checkout means."""
    days = build(_package(), arrival=ARRIVAL, departure=DEPARTURE)
    last = days[-1]
    assert last.property_name == "Maasai Mara Hotel"
    assert last.board == ""
    assert not last.has_night


def test_board_is_the_board_of_the_leg_holding_that_night():
    """Bed and breakfast in Nairobi, full board in the Mara.

    A package's plan is a per-leg decision (§3.9), so a day-by-day that reads
    it off the option would print the wrong board for every leg but the first.
    """
    days = build(_package(), arrival=ARRIVAL, departure=DEPARTURE)
    assert [day.board for day in days] == ["BB", "FB", "FB", ""]


def test_legs_are_read_in_sequence_and_not_in_date_order():
    """A mis-sequenced package is not silently repaired.

    Sorting by date would make a typo look like a valid trip; `packages.check`
    is what reports it, and this must not hide the evidence first.
    """
    swapped = [
        _leg(2, "Nairobi", ARRIVAL, date(2026, 7, 2), board="BB"),
        _leg(1, "Maasai Mara", date(2026, 7, 2), DEPARTURE),
    ]
    days = build(swapped, arrival=ARRIVAL, departure=DEPARTURE)
    # The 1st is held only by the leg whose dates cover it, whatever its number.
    assert days[0].destination == "Nairobi"
    assert days[0].leg == 2


def test_a_day_no_leg_covers_is_left_blank_rather_than_guessed():
    """A hole is `packages.check`'s to report, not this module's to fill.

    Inventing a property over a gap would hide a night the client has no bed
    for behind a plausible-looking page.
    """
    days = build(
        [_leg(1, "Diani", ARRIVAL, date(2026, 7, 2))],
        arrival=ARRIVAL,
        departure=DEPARTURE,
    )
    assert days[1].destination == ""
    assert days[1].property_name == ""
    assert days[1].leg is None
    # The last day still finds the property they woke up in.
    assert days[-1].destination == "Diani"


# --------------------------------------------------------------------------- #
# Movements and excursions on the calendar
# --------------------------------------------------------------------------- #


def test_a_movement_lands_on_the_date_it_runs():
    days = build(
        _single(),
        arrival=ARRIVAL,
        departure=DEPARTURE,
        movements=[
            Movement(1, "transfer", "road", "Airport to hotel", on=ARRIVAL),
            Movement(2, "transfer", "road", "Hotel to airport", on=DEPARTURE),
        ],
    )
    assert [one.label for one in days[0].movements] == ["Airport to hotel"]
    assert days[1].movements == ()
    assert [one.label for one in days[-1].movements] == ["Hotel to airport"]


def test_an_undated_movement_appears_on_no_day_and_is_reported():
    """The alternative was worse, and a real document proved it.

    Undated movements went on the arrival day first, on the reasoning that the
    arrival date is the tariff they are priced at (§3.10). A rail return with
    its four mandatory transfers then put all six movements on day one, so the
    page told the client their transfer home was the day they landed. An
    incomplete programme with an advisory against it is honest; a confident
    wrong one is not.
    """
    movements = [Movement(1, "line_haul", "rail", "Nairobi to Mombasa")]
    days = build(
        _single(), arrival=ARRIVAL, departure=DEPARTURE, movements=movements
    )
    assert all(day.movements == () for day in days)
    problems = check(arrival=ARRIVAL, departure=DEPARTURE, movements=movements)
    assert [p.code for p in problems] == [MOVEMENT_UNDATED]
    assert not problems[0].blocking
    assert "does not appear on the day-by-day" in problems[0].message


def test_a_movement_dated_outside_the_trip_is_blocking():
    """It prices off a tariff window that is not the one it will be charged at."""
    problems = check(
        arrival=ARRIVAL,
        departure=DEPARTURE,
        movements=[
            Movement(1, "transfer", "road", "Airport to hotel", on=date(2026, 8, 9))
        ],
    )
    assert [p.code for p in problems] == [MOVEMENT_OFF_TRIP]
    assert problems[0].blocking
    assert problems[0].sequence == 1


def test_several_movements_on_one_day_keep_their_order():
    """Rail drags two transfers with it (§3.10), and they happen in sequence."""
    days = build(
        _single(),
        arrival=ARRIVAL,
        departure=DEPARTURE,
        movements=[
            Movement(3, "transfer", "road", "Terminus to hotel", on=ARRIVAL),
            Movement(1, "transfer", "road", "Hotel to terminus", on=ARRIVAL),
            Movement(2, "line_haul", "rail", "SGR", on=ARRIVAL),
        ],
    )
    assert [one.label for one in days[0].movements] == [
        "Hotel to terminus",
        "SGR",
        "Terminus to hotel",
    ]


def test_an_excursion_lands_on_the_day_number_it_was_scheduled_for():
    days = build(
        _single(),
        arrival=ARRIVAL,
        departure=DEPARTURE,
        excursions=[Excursion("Sunset dhow cruise", day=2)],
    )
    assert days[0].excursions == ()
    assert days[1].excursions == ("Sunset dhow cruise",)


def test_an_excursion_scheduled_off_the_trip_is_blocking():
    """Day nine of a four-day trip: the fare came from the wrong date.

    Invisible on a finished document — the excursion is listed, the price looks
    ordinary — which is exactly why it cannot be an advisory.
    """
    problems = check(
        arrival=ARRIVAL,
        departure=DEPARTURE,
        excursions=[Excursion("Wasini Island day", day=9)],
    )
    assert [p.code for p in problems] == [ACTIVITY_OFF_TRIP]
    assert problems[0].blocking
    assert "day 9" in problems[0].message and "4 day(s)" in problems[0].message


def test_the_last_day_is_a_valid_day_for_an_excursion():
    """Off-by-one guard: day 4 of a four-day trip is the departure day.

    A morning excursion before an evening flight is an ordinary itinerary, so
    the range has to include it.
    """
    assert (
        check(
            arrival=ARRIVAL,
            departure=DEPARTURE,
            excursions=[Excursion("Farewell brunch cruise", day=4)],
        )
        == []
    )


def test_an_unscheduled_excursion_is_charged_and_reported():
    """It has no day, so it cannot be placed — but it is still being paid for.

    Advisory rather than blocking: the price is right and the programme is
    incomplete, which is a document to finish rather than a figure to fix.
    """
    excursions = [Excursion("Reef snorkelling", day=None)]
    days = build(
        _single(), arrival=ARRIVAL, departure=DEPARTURE, excursions=excursions
    )
    assert all(day.excursions == () for day in days)
    problems = check(arrival=ARRIVAL, departure=DEPARTURE, excursions=excursions)
    assert [p.code for p in problems] == [ACTIVITY_UNSCHEDULED]
    assert not problems[0].blocking
    assert "charged but does not appear" in problems[0].message


def test_two_excursions_on_one_day_both_appear():
    days = build(
        _single(),
        arrival=ARRIVAL,
        departure=DEPARTURE,
        excursions=[
            Excursion("Reef snorkelling", day=2),
            Excursion("Sunset dhow cruise", day=2),
        ],
    )
    assert days[1].excursions == ("Reef snorkelling", "Sunset dhow cruise")


# --------------------------------------------------------------------------- #
# The two together
# --------------------------------------------------------------------------- #


def test_programme_returns_the_days_and_the_faults_together():
    """A caller with one almost always wants the other."""
    result = programme(
        _package(),
        arrival=ARRIVAL,
        departure=DEPARTURE,
        movements=[Movement(1, "transfer", "road", "Airport to hotel")],
        excursions=[Excursion("Balloon safari", day=3)],
    )
    # The undated movement is the advisory below, and appears on no day.
    assert len(result.days) == 4
    assert result.days[2].excursions == ("Balloon safari",)
    assert [p.code for p in result.problems] == [MOVEMENT_UNDATED]


def test_the_programme_is_the_same_twice():
    """Frozen into a version snapshot, so it cannot depend on anything ambient."""
    kwargs = {
        "arrival": ARRIVAL,
        "departure": DEPARTURE,
        "movements": [Movement(1, "transfer", "road", "Airport to hotel", on=ARRIVAL)],
        "excursions": [Excursion("Balloon safari", day=3)],
    }
    assert build(_package(), **kwargs) == build(_package(), **kwargs)


def test_the_journey_is_laid_onto_every_package_unchanged():
    """The same movements, whichever hotel the client picks.

    Transport belongs to the quote and is charged into every option (§3.10), so
    a day-by-day that differed between options would contradict the price.
    """
    movements = [Movement(1, "transfer", "road", "Airport to hotel", on=ARRIVAL)]
    one = build(_single(), arrival=ARRIVAL, departure=DEPARTURE, movements=movements)
    two = build(_package(), arrival=ARRIVAL, departure=DEPARTURE, movements=movements)
    assert one[0].movements == two[0].movements


def test_the_day_a_package_changes_hotels_names_both():
    """The one thing on a day-by-day a client cannot work out for themselves.

    The 2nd of July belongs to the Mara — it is the night they sleep there
    (§3.9) — so without this the day reads as though they woke up in the Mara,
    and the move is invisible on the page.
    """
    days = build(_package(), arrival=ARRIVAL, departure=DEPARTURE)
    assert days[0].moves_from == ""
    assert days[1].moves_from == "Nairobi Hotel"
    assert days[1].property_name == "Maasai Mara Hotel"
    # Not a move: the remaining days are the same leg.
    assert [day.moves_from for day in days[2:]] == ["", ""]


def test_a_single_property_option_never_moves():
    days = build(_single(), arrival=ARRIVAL, departure=DEPARTURE)
    assert all(day.moves_from == "" for day in days)


def test_a_gap_does_not_invent_a_move():
    """A day no leg covers has nowhere to move from or to.

    The leg either side is the same one, so reporting a move on the day after
    the hole would describe a journey the client is not making.
    """
    days = build(
        [
            _leg(1, "Diani", ARRIVAL, date(2026, 7, 2)),
            _leg(2, "Diani", date(2026, 7, 3), DEPARTURE),
        ],
        arrival=ARRIVAL,
        departure=DEPARTURE,
    )
    assert days[1].leg is None
    assert days[1].moves_from == ""
    # And the next covered day does report the change of leg, which is real:
    # two stays at one destination with a night missing between them.
    assert days[2].moves_from == "Diani Hotel"
