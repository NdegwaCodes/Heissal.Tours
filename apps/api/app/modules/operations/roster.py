"""Who and what is on a trip, and what would stop it going. Pure (§8.1).

§2.5 put vehicles in the database as a **costing input**: a Land Cruiser has a
fuel consumption and a daily operating cost, and §4.2 charges a drive with
them. Nothing anywhere said that a particular vehicle was *busy*. Two bookings
could be priced with the same Land Cruiser over the same week and the first
anybody would know is a Tuesday morning in Diani with one vehicle and two
groups.

So this module answers three questions, and refuses a fourth.

**Is this vehicle or this person already out?** An overlap on the dates, which
is the whole of it — and the one subtlety worth the code is that sharing a
single boundary day is not the same as overlapping. A vehicle that drops a
group at the airport on the 5th and collects another that afternoon is a normal
Tuesday at a coast operator; a vehicle on two trips over the 5th and 6th is a
Tuesday that does not happen. The first is reported, the second refused.

**Can they legally and physically do it?** A driver whose licence expires *in
the middle of the safari* is the case worth catching — checking it against
today passes, and the group is in Tsavo on the day it lapses. Seats are counted
across every vehicle on the booking, not per vehicle, because a group of twelve
in two Land Cruisers is the normal answer rather than a problem.

**What is still missing before Thursday?** The operational equivalent of §5.2's
morning list: no vehicle, no driver, not enough seats, a licence about to go.

And the fourth, refused: **nothing here decides that a trip is ready.** It
reports what is missing against thresholds an operator sets. Whether a trip
with no guide assigned is a problem depends on whether the client asked for
one, and that is a judgement the person reading the list makes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

#: What somebody is doing on a trip. A **driver_guide** is one role and not two
#: because in this market it is usually one person, and modelling it as a
#: driver row plus a guide row would mean assigning them twice, double-booking
#: them against themselves, and counting them twice on a cost sheet.
DRIVER = "driver"
GUIDE = "guide"
DRIVER_GUIDE = "driver_guide"
CREW_ROLES = (DRIVER, GUIDE, DRIVER_GUIDE)

#: Which roles satisfy a need for a driver, and which for a guide.
DRIVES = (DRIVER, DRIVER_GUIDE)
GUIDES = (GUIDE, DRIVER_GUIDE)

# What is wrong with a trip, or with an assignment somebody is attempting.
NO_VEHICLE = "trip_no_vehicle"
NO_DRIVER = "trip_no_driver"
NOT_ENOUGH_SEATS = "trip_seats_short"
LICENCE_EXPIRING = "crew_licence_expires_during_trip"
TIGHT_TURNAROUND = "assignment_tight_turnaround"


class AssignmentRefused(ValueError):
    """An assignment the rules will not allow, with the reason."""


@dataclass(frozen=True)
class Window:
    """The days something is committed for, both ends inclusive.

    Inclusive because an operator says "the 4th to the 8th" and means five
    days. A half-open convention would be defensible and would also mean every
    conversation about a clash starts by agreeing what the dates mean.
    """

    starts_on: date
    ends_on: date

    def __post_init__(self) -> None:
        if self.ends_on < self.starts_on:
            raise AssignmentRefused(
                "An assignment cannot end before it starts."
            )

    @property
    def days(self) -> int:
        return (self.ends_on - self.starts_on).days + 1

    def shared_days(self, other: Window) -> int:
        """How many days the two have in common. Zero where they do not meet."""
        first = max(self.starts_on, other.starts_on)
        last = min(self.ends_on, other.ends_on)
        return max((last - first).days + 1, 0)

    def is_handover(self, other: Window) -> bool:
        """Whether the two meet on exactly one day, as a handover.

        One window's last day is the other's first, and neither window *is*
        that day. Both halves matter: dropping the second would make two
        single-day trips on the same date read as a handover, which is two
        groups and one vehicle.
        """
        return (
            self.shared_days(other) == 1
            and self.starts_on != other.starts_on
            and self.ends_on != other.ends_on
        )

    def overlaps(self, other: Window) -> bool:
        """Whether the two cannot both happen.

        Any shared day, **except** a single day that is one window's last and
        the other's first. A vehicle dropping a group at the airport on the 5th
        and collecting another that afternoon is a normal Tuesday at the coast;
        a vehicle on two trips over the 5th and 6th is a Tuesday that does not
        happen.
        """
        return self.shared_days(other) > 0 and not self.is_handover(other)


@dataclass(frozen=True)
class Held:
    """One existing commitment, as the clash rules see it."""

    window: Window
    #: What it is held for, so a message can name the trip rather than an id.
    reference: str = ""
    booking_id: uuid.UUID | None = None
    subject: str = ""


@dataclass(frozen=True)
class Clash:
    """A conflict, or a handover tight enough to mention."""

    code: str
    message: str
    reference: str = ""
    #: ``True`` where this stops the assignment; ``False`` where it is advice.
    blocking: bool = True


def clashes(
    wanted: Window,
    existing: Iterable[Held],
    *,
    subject: str = "This",
    ignore: uuid.UUID | None = None,
) -> list[Clash]:
    """Every reason ``wanted`` conflicts with what is already booked.

    ``ignore`` skips one existing commitment, which is what re-dating an
    assignment needs: without it, every move would clash with the assignment
    being moved.
    """
    found: list[Clash] = []
    for held in existing:
        if ignore is not None and held.booking_id == ignore:
            continue
        if wanted.overlaps(held.window):
            found.append(
                Clash(
                    "assignment_clash",
                    f"{subject} is already out "
                    f"{held.window.starts_on:%d %b} to "
                    f"{held.window.ends_on:%d %b}"
                    + (f" on {held.reference}" if held.reference else "")
                    + ". Two trips over the same days is one trip that does "
                    "not happen.",
                    reference=held.reference,
                )
            )
        elif wanted.is_handover(held.window):
            found.append(
                Clash(
                    TIGHT_TURNAROUND,
                    f"{subject} finishes and starts again on the same day "
                    + (f"({held.reference})" if held.reference else "")
                    + ". Normal at the coast, worth a look if the two are far "
                    "apart.",
                    reference=held.reference,
                    blocking=False,
                )
            )
    return found


# --------------------------------------------------------------------------- #
# Who and what may be assigned
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Crew:
    """A person as the rules see them."""

    name: str
    roles: tuple[str, ...] = ()
    is_active: bool = True
    licence_expires_on: date | None = None

    def can(self, role: str) -> bool:
        if role == DRIVER:
            return any(one in DRIVES for one in self.roles)
        if role == GUIDE:
            return any(one in GUIDES for one in self.roles)
        return role in self.roles


def check_crew(member: Crew, role: str, window: Window) -> list[Clash]:
    """Whether this person may take this role over these dates.

    The licence is checked against the **end** of the trip, not against today.
    A licence that expires on the Thursday of a safari passes every check made
    on the Monday, and the group is in Tsavo when it lapses — which is the
    whole reason the date is stored rather than a boolean.
    """
    if not member.is_active:
        raise AssignmentRefused(
            f"{member.name} is not on the active roster, so they cannot be put "
            f"on a trip. Reactivate them first if that is wrong."
        )
    if role not in CREW_ROLES:
        raise AssignmentRefused(
            f"'{role}' is not something somebody does on a trip. Say "
            f"{', '.join(CREW_ROLES)}."
        )
    if not member.can(role):
        raise AssignmentRefused(
            f"{member.name} is not down as a {role.replace('_', ' ')}. A guide "
            f"who does not drive cannot be sent out with the vehicle."
        )
    out: list[Clash] = []
    if member.licence_expires_on is not None and role in DRIVES:
        if member.licence_expires_on < window.starts_on:
            raise AssignmentRefused(
                f"{member.name}'s licence expired on "
                f"{member.licence_expires_on:%d %B %Y}, before this trip even "
                f"starts."
            )
        if member.licence_expires_on <= window.ends_on:
            raise AssignmentRefused(
                f"{member.name}'s licence expires on "
                f"{member.licence_expires_on:%d %B %Y}, in the middle of this "
                f"trip. Renew it or send somebody else — the group would be "
                f"out there on the day it lapses."
            )
    return out


@dataclass(frozen=True)
class Fleet:
    """A vehicle as the rules see it."""

    name: str
    vehicle_type: str = ""
    passenger_capacity: int = 0
    is_active: bool = True


def check_vehicle(vehicle: Fleet) -> None:
    if not vehicle.is_active:
        raise AssignmentRefused(
            f"{vehicle.name} is not in the active fleet, so it cannot be put "
            f"on a trip."
        )


def seats(vehicles: Iterable[Fleet]) -> int:
    """Seats across every vehicle on the booking.

    Counted together rather than per vehicle, because a group of twelve in two
    Land Cruisers is the normal answer and a per-vehicle check would refuse it.
    """
    return sum(max(one.passenger_capacity, 0) for one in vehicles)


# --------------------------------------------------------------------------- #
# What is missing before Thursday
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Gap:
    """One thing standing between a booking and a trip that can leave."""

    code: str
    message: str
    #: Days until departure, so the list can be read worst-first.
    days: int = 0


@dataclass
class Roster:
    """What is on a trip, and what is not."""

    reference: str = ""
    departs_on: date | None = None
    pax_count: int = 0
    vehicles: list[Fleet] = field(default_factory=list)
    drivers: list[Crew] = field(default_factory=list)
    guides: list[Crew] = field(default_factory=list)

    @property
    def seats(self) -> int:
        return seats(self.vehicles)


def check_ready(
    roster: Roster, *, today: date, licence_warning_days: int = 30
) -> list[Gap]:
    """What is still missing, worst first.

    Missing a **guide** is deliberately not reported. Whether a trip needs one
    depends on whether the client asked and paid for one, and a list that cried
    about every self-drive booking is a list nobody opens — the §5.2 lesson
    about closing leads on a timer, applied to a departure board.
    """
    out: list[Gap] = []
    until = (roster.departs_on - today).days if roster.departs_on else 0

    if not roster.vehicles:
        out.append(
            Gap(
                NO_VEHICLE,
                f"{roster.reference} has no vehicle and leaves in "
                f"{until} day(s).",
                days=until,
            )
        )
    if not roster.drivers:
        out.append(
            Gap(
                NO_DRIVER,
                f"{roster.reference} has no driver and leaves in "
                f"{until} day(s).",
                days=until,
            )
        )
    if roster.vehicles and roster.pax_count > roster.seats:
        short = roster.pax_count - roster.seats
        out.append(
            Gap(
                NOT_ENOUGH_SEATS,
                f"{roster.reference} carries {roster.pax_count} and the "
                f"assigned vehicles seat {roster.seats} — {short} short. Add "
                f"another vehicle.",
                days=until,
            )
        )
    for member in roster.drivers:
        if member.licence_expires_on is None or roster.departs_on is None:
            continue
        notice = (member.licence_expires_on - roster.departs_on).days
        if 0 <= notice <= licence_warning_days:
            out.append(
                Gap(
                    LICENCE_EXPIRING,
                    f"{member.name}'s licence expires {notice} day(s) after "
                    f"{roster.reference} departs. It will not stop this trip; "
                    f"it will stop the next one.",
                    days=until,
                )
            )
    return out


def sort_gaps(gaps: Sequence[Gap]) -> list[Gap]:
    """Soonest departure first, then the worst kind of gap.

    An unsorted departure board is a departure board nobody works through —
    the same argument as §5.2's morning list.
    """
    rank = {NO_VEHICLE: 0, NO_DRIVER: 1, NOT_ENOUGH_SEATS: 2, LICENCE_EXPIRING: 3}
    return sorted(gaps, key=lambda one: (one.days, rank.get(one.code, 9)))


def window_for(
    arrival: date, departure: date, *, before_days: int = 0, after_days: int = 0
) -> Window:
    """The days a vehicle and crew are committed for, given the trip's dates.

    Wider than the client's dates where an operator says so: a vehicle leaving
    Nairobi the night before a coast pickup is out that night, and a fleet
    calendar that says otherwise will hand it to somebody else.
    """
    return Window(
        starts_on=arrival - timedelta(days=max(before_days, 0)),
        ends_on=departure + timedelta(days=max(after_days, 0)),
    )


__all__ = [
    "CREW_ROLES",
    "DRIVER",
    "DRIVER_GUIDE",
    "DRIVES",
    "GUIDE",
    "GUIDES",
    "LICENCE_EXPIRING",
    "NOT_ENOUGH_SEATS",
    "NO_DRIVER",
    "NO_VEHICLE",
    "TIGHT_TURNAROUND",
    "AssignmentRefused",
    "Clash",
    "Crew",
    "Fleet",
    "Gap",
    "Held",
    "Roster",
    "Window",
    "check_crew",
    "check_ready",
    "check_vehicle",
    "clashes",
    "seats",
    "sort_gaps",
    "window_for",
]
