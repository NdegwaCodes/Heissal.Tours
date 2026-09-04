"""Road routes: the rules, as pure functions (§4.2).

Stage 3.10 put transport in the price and left one hole in it. A movement run on
a **hired** vehicle is priced from a transfer tariff; a movement run on **our
own** vehicle was skipped entirely, on the reasoning that the Stage 2 fleet
model would cost it — and the Stage 2 model is not in the Stage 3 build-up. So
an option whose group is driven to the Mara in the company Land Cruiser carried
the beds, the park fees and nothing at all for the eight-hour drive.

What was missing to close it was the distance. The catalogue holds latitude and
longitude, which cannot answer it: Nairobi to the Mara is about 225 km straight
and about 270 km driven, and the time depends on the surface far more than on
either figure. So distance and drive time are **hand-entered per route** by the
people who drive them, along with the vehicle types the road takes.

This module holds the arithmetic and the judgements that follow from that table.
It does no I/O and knows nothing about the database, so the rule that decides
whether a quote can be issued is testable without one.

The fuel arithmetic itself is deliberately **not** here:
:func:`app.modules.vehicles.service.compute_transport_cost` has done it since
Stage 2.8 and is already unit-tested. A second implementation of litres times
price is exactly the kind of duplication that ends with two answers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.modules.quotes.packages import Problem

# Problem codes, so a caller acts on the fault rather than parsing a sentence.
NO_ROUTE = "route_not_on_file"
NO_ENDPOINTS = "route_endpoints_missing"
WRONG_VEHICLE = "route_vehicle_not_suitable"
NO_FUEL_PRICE = "route_fuel_price_missing"

#: A movement occupies its vehicle and driver for the day it runs. One, not a
#: figure derived from the drive time: a ten-hour drive to the Mara and a
#: two-hour transfer both cost a day of the driver's time, because neither
#: leaves the day free for another job. A multi-day drive is entered as the
#: movements it actually is, which is also how the itinerary reads it.
DAYS_PER_MOVEMENT = 1


def plain(value: Decimal) -> str:
    """A stored figure as a person would write it: 270, not 270.00.

    ``NUMERIC`` columns carry their scale, so a distance reads "270.00 km" and
    a consumption "8.0000 km/L" straight out of the database. These strings go
    on a worksheet an operator reconciles by eye, and four trailing zeros are
    four chances to misread a figure.
    """
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class Road:
    """One route as the rules see it — the row, flattened and direction-aware.

    ``reversed_lookup`` records that the row found was entered the other way
    round. Distance is symmetric and time roughly is, so reading a row
    backwards is right far more often than refusing to; saying so keeps it
    honest on the worksheet, where an operator reconciling a fuel figure needs
    to know which row it came from and which way.
    """

    label: str
    distance_km: Decimal
    drive_time_minutes: int
    required_vehicle_types: tuple[str, ...] = ()
    reversed_lookup: bool = False
    notes: str = ""
    source: str = ""

    @property
    def hours(self) -> Decimal:
        """Drive time in hours, to two places. For a rate, never for a promise."""
        return (Decimal(self.drive_time_minutes) / Decimal(60)).quantize(
            Decimal("0.01")
        )


def normalise_types(values: Iterable[str] | None) -> tuple[str, ...]:
    """Vehicle types as a clean, ordered, de-duplicated tuple.

    Hand-entered, so trimmed and lower-cased at the boundary — ``"4x4 "`` and
    ``"4X4"`` are one requirement, and a quote refused because of a trailing
    space would be a rule nobody could satisfy.
    """
    seen: list[str] = []
    for value in values or ():
        cleaned = (value or "").strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def vehicle_is_suitable(required: Iterable[str] | None, offered: str | None) -> bool:
    """Whether ``offered`` is one of the vehicle types this road takes.

    An empty requirement means the road takes anything, which is most tarmac.
    An offered type of nothing at all — a movement with no vehicle named —
    cannot be judged suitable against a stated requirement: silence is not
    compliance, and the alternative is a saloon sent up a road that needs a
    Land Cruiser.
    """
    wanted = normalise_types(required)
    if not wanted:
        return True
    return (offered or "").strip().lower() in wanted


def check_vehicle(
    *,
    label: str,
    road: Road,
    offered: str | None,
    sequence: int | None = None,
) -> Problem | None:
    """The fault where a movement's vehicle is not one the road takes.

    **Blocking**, and it is the reason the column exists. It is two failures at
    once: the quote is under-priced, because the vehicle the trip actually
    needs costs more than the one it was costed on, and the trip does not
    happen as sold. Both are invisible on a finished document — a saloon and a
    Land Cruiser look identical on a proposal — and the second is discovered
    on the road.
    """
    if vehicle_is_suitable(road.required_vehicle_types, offered):
        return None
    takes = ", ".join(road.required_vehicle_types)
    named = (offered or "").strip() or "no vehicle"
    return Problem(
        WRONG_VEHICLE,
        f"{label} is quoted on {named}, but {road.label or 'this route'} takes "
        f"{takes}. The quote is under-priced by the difference and the drive "
        f"cannot be run as sold."
        + (f" Route note: {road.notes}" if road.notes else ""),
        sequence=sequence,
    )


def check_endpoints(
    *, label: str, has_origin: bool, has_destination: bool, sequence: int | None = None
) -> Problem | None:
    """The fault where a road movement on our own vehicle has no route to find.

    A route is a **pair**, and the far end cannot be inferred: transport is
    priced once for the quote (§3.10) while each option has its own legs, so
    "the previous destination" is a different place depending on which option
    is being priced. Asking for it is one field; guessing it is a fuel figure
    for a drive nobody is making.
    """
    if has_origin and has_destination:
        return None
    missing = "start" if not has_origin else "destination"
    return Problem(
        NO_ENDPOINTS,
        f"{label} is driven on our own vehicle but names no {missing}, so its "
        f"route — and with it the distance, the drive time and the fuel — "
        f"cannot be found. Set both ends of the movement.",
        sequence=sequence,
    )


def missing_route(
    *, label: str, sequence: int | None = None, on: object | None = None
) -> Problem:
    """The fault where the pair is stated and no route row covers it.

    Blocking for the same reason a missing tariff is (§3.10): the movement then
    carries no cost, and a zero on a document reads as a leg the client is not
    being charged for rather than as a gap in our own data.
    """
    when = f" on {on}" if on is not None else ""
    return Problem(
        NO_ROUTE,
        f"{label} has no route on file for those two places{when}, so the "
        f"drive carries no distance and no fuel. Enter the route — the driven "
        f"kilometres, the time it takes, and the vehicles the road takes.",
        sequence=sequence,
    )


def missing_fuel_price(
    *, label: str, fuel_type: str, sequence: int | None = None
) -> Problem:
    """No fuel price on file for the vehicle's fuel on the day it drives.

    Blocking, and worth its own code rather than folding into "unpriced": the
    route is there and the vehicle is right, and what is missing is one row in
    a table somebody updates monthly.
    """
    return Problem(
        NO_FUEL_PRICE,
        f"{label} is driven on a {fuel_type} vehicle and no {fuel_type} price "
        f"is on file for that date, so the fuel is uncosted. Load the pump "
        f"price.",
        sequence=sequence,
    )
