"""The day-by-day programme, as pure functions (Stage 4.1).

Everything the quote engine needed to price a trip is now in place: legs with
dates (§3.9), a group vector (§3.8), a costed journey (§3.10) and excursions the
client cannot decline (§3.8's last piece). What none of it produced is the thing
a client actually reads before looking at the price — **what happens on which
day**.

It is derivation, not new data. A package already says which property holds
which nights, each movement already carries the date it runs, and each selected
activity already carries the day it falls on: laying those on a calendar is the
only way to see that they agree. Which is the second reason this exists — the
day numbers were being *priced against* before anything checked them. An
excursion scheduled on day nine of a five-night trip picks a fare from a date
the client is not in the country, and nothing anywhere said so.

Three rules decide the shape:

* **A trip has one more day than it has nights.** Arrival and departure are both
  days the client spends travelling, and the departure day has a movement, a
  checkout and no bed. Counting days as nights loses the last day entirely,
  which is where the flight home is.
* **A day belongs to the leg that holds its night.** ``check_out`` is the
  morning the guest leaves, so the date belongs to the leg they slept under the
  night before — the same convention that makes two contiguous legs share a date
  without double-charging it (§3.9).
* **Words are the document's job.** This module names board by its plan *code*
  and a movement by its own label; how a proposal phrases "full board" or
  "breakfast, then checkout" belongs with the rest of the client-facing wording,
  not here.

No I/O, and no clock: two runs over the same quote produce the same programme,
which is what lets the version snapshot freeze it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.modules.quotes.packages import Leg, Problem, order

# Problem codes, so a caller acts on the fault rather than parsing a sentence.
NO_DAYS = "itinerary_no_days"
ACTIVITY_OFF_TRIP = "itinerary_activity_off_trip"
ACTIVITY_UNSCHEDULED = "itinerary_activity_unscheduled"
MOVEMENT_OFF_TRIP = "itinerary_movement_off_trip"
MOVEMENT_UNDATED = "itinerary_movement_undated"


@dataclass(frozen=True)
class Movement:
    """One journey on the programme — a transfer or a line haul (§3.10).

    ``on`` is the date it runs, and ``None`` means the agent did not say — in
    which case it appears on no day at all, and is reported instead.

    That was the other way round first: undated movements went on the arrival
    day, because the arrival date is the tariff they are **priced** at (§3.10).
    A rail return with its four mandatory transfers showed what is wrong with
    it — all six movements piled onto day one, so the client's page said the
    transfer home happens the day they land. An incomplete programme with an
    advisory against it is honest; a confident wrong one is not, and pricing at
    the arrival tariff is a separate question from which day a page claims.
    """

    sequence: int
    kind: str
    mode: str
    label: str
    on: date | None = None
    is_optional: bool = False


@dataclass(frozen=True)
class Excursion:
    """One activity on the programme, and the day the agent put it on.

    ``day`` is a day number rather than a date: it is what the agent enters and
    what the fare is selected against, so keeping it in that form means the
    programme checks the same value pricing used rather than a re-derivation of
    it.
    """

    name: str
    day: int | None = None
    is_mandatory: bool = True


@dataclass(frozen=True)
class Day:
    """One day of the trip, with everything that happens on it."""

    number: int
    on: date
    destination: str = ""
    property_name: str = ""
    #: The meal plan code of the leg whose night this day belongs to. Empty on
    #: the departure day, which has no night under it.
    board: str = ""
    #: Which leg of the package holds this day, for a document that groups them.
    leg: int | None = None
    #: The property left that morning, on a day the package changes hotels.
    #: The day belongs to the leg whose night it is (§3.9), so without this a
    #: transfer day reads as though the client woke up where they went to bed —
    #: and the move between two hotels is the one thing on a day-by-day that
    #: the client cannot work out for themselves.
    moves_from: str = ""
    movements: tuple[Movement, ...] = ()
    excursions: tuple[str, ...] = ()
    is_arrival: bool = False
    is_departure: bool = False

    @property
    def has_night(self) -> bool:
        """Whether the client sleeps at ``property_name`` tonight."""
        return not self.is_departure


def dates(*, arrival: date, departure: date) -> list[date]:
    """Every date of the trip, arrival and departure days included.

    One more than the nights: a 1–4 July booking is three nights and four days,
    and the fourth is the one the client flies home on.
    """
    span = (departure - arrival).days
    if span < 0:
        return []
    return [arrival + timedelta(days=n) for n in range(span + 1)]


def build(
    legs: list[Leg],
    *,
    arrival: date,
    departure: date,
    movements: list[Movement] | None = None,
    excursions: list[Excursion] | None = None,
) -> list[Day]:
    """The programme for one package.

    Per package, not per quote: the legs differ between options, so the day a
    client is in the Mara depends on which option they choose. The journey and
    the excursions belong to the quote and are laid onto each package's days
    unchanged — the same movements and the same excursions, whichever hotel.

    A day with no leg over it is left blank rather than guessed at. Coverage is
    already checked — and blocking — in :func:`app.modules.quotes.packages.check`,
    so a hole here means that check has something to say and this should not
    invent an answer over the top of it.
    """
    calendar = dates(arrival=arrival, departure=departure)
    if not calendar:
        return []

    ordered = order(legs)
    per_day_movements: dict[date, list[Movement]] = {}
    for movement in sorted(movements or [], key=lambda one: one.sequence):
        # Only where the agent said which day. See ``Movement`` for why an
        # undated one is left off rather than assumed onto the arrival day.
        if movement.on is None:
            continue
        per_day_movements.setdefault(movement.on, []).append(movement)

    per_day_excursions: dict[int, list[str]] = {}
    for excursion in excursions or []:
        if excursion.day is None:
            continue
        per_day_excursions.setdefault(excursion.day, []).append(excursion.name)

    out: list[Day] = []
    previous: Leg | None = None
    for index, when in enumerate(calendar, start=1):
        # The leg whose night this is. The departure day has no night, so it is
        # shown under the property the client woke up in — which is the last
        # leg, and is what "checkout" means.
        holding = next(
            (leg for leg in ordered if leg.check_in <= when < leg.check_out), None
        )
        is_departure = when == departure
        if holding is None and is_departure and ordered:
            holding = ordered[-1]
        moved = (
            previous.property_name
            if previous is not None
            and holding is not None
            and previous.sequence != holding.sequence
            else ""
        )
        if holding is not None:
            previous = holding
        out.append(
            Day(
                number=index,
                on=when,
                destination=holding.destination if holding else "",
                property_name=holding.property_name if holding else "",
                board="" if is_departure or holding is None else holding.board,
                leg=holding.sequence if holding else None,
                moves_from=moved,
                movements=tuple(per_day_movements.get(when, ())),
                excursions=tuple(per_day_excursions.get(index, ())),
                is_arrival=when == arrival,
                is_departure=is_departure,
            )
        )
    return out


def check(
    *,
    arrival: date,
    departure: date,
    movements: list[Movement] | None = None,
    excursions: list[Excursion] | None = None,
) -> list[Problem]:
    """Every fault in the programme's own data.

    Deliberately not a re-check of the legs: coverage and contiguity belong to
    :mod:`app.modules.quotes.packages` and a second implementation of them would
    be a second thing to keep in step. What is checked here is only what the
    day-by-day is the first thing to know — whether the days an agent typed are
    days of *this* trip.

    The two blocking faults are both mis-prices rather than presentation
    problems. An excursion on day nine of a five-night trip has its fare
    selected from a date the client is not in the country, and a movement dated
    outside the stay is priced off a tariff window that may not be the one it
    will actually be charged at. Both are invisible on a finished document,
    which is what makes them blocking rather than advisory.
    """
    problems: list[Problem] = []
    calendar = dates(arrival=arrival, departure=departure)
    if not calendar:
        return [
            Problem(
                NO_DAYS,
                f"The group departs on {departure}, which is not after the "
                f"arrival on {arrival}, so the trip has no days.",
            )
        ]

    total = len(calendar)
    for excursion in excursions or []:
        if excursion.day is None:
            problems.append(
                Problem(
                    ACTIVITY_UNSCHEDULED,
                    f"{excursion.name} is on the quote with no day, so it is "
                    f"charged but does not appear on the day-by-day. Set the "
                    f"day it falls on.",
                    blocking=False,
                )
            )
            continue
        if not 1 <= excursion.day <= total:
            problems.append(
                Problem(
                    ACTIVITY_OFF_TRIP,
                    f"{excursion.name} is scheduled for day {excursion.day}, "
                    f"but this trip is {total} day(s) long. Its fare is being "
                    f"selected for a date the group is not here.",
                )
            )

    for movement in sorted(movements or [], key=lambda one: one.sequence):
        if movement.on is None:
            problems.append(
                Problem(
                    MOVEMENT_UNDATED,
                    f"{movement.label or movement.mode} has no travel date, so "
                    f"it is priced at the arrival date's tariff and does not "
                    f"appear on the day-by-day. Set the day it runs.",
                    sequence=movement.sequence,
                    blocking=False,
                )
            )
            continue
        if not arrival <= movement.on <= departure:
            problems.append(
                Problem(
                    MOVEMENT_OFF_TRIP,
                    f"{movement.label or movement.mode} is dated "
                    f"{movement.on}, outside the {arrival} to {departure} "
                    f"trip, so it prices off the wrong tariff window.",
                    sequence=movement.sequence,
                )
            )

    return problems


@dataclass
class Programme:
    """A package's days plus the faults found while laying them out.

    Returned together because a caller that has one almost always wants the
    other: the document renders the days, and readiness reports the faults.
    """

    days: list[Day] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def programme(
    legs: list[Leg],
    *,
    arrival: date,
    departure: date,
    movements: list[Movement] | None = None,
    excursions: list[Excursion] | None = None,
) -> Programme:
    """:func:`build` and :func:`check` in one call, over one set of inputs."""
    return Programme(
        days=build(
            legs,
            arrival=arrival,
            departure=departure,
            movements=movements,
            excursions=excursions,
        ),
        problems=check(
            arrival=arrival,
            departure=departure,
            movements=movements,
            excursions=excursions,
        ),
    )
