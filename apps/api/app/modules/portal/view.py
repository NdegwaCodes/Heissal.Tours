"""What a client may see of their own trip. Pure functions (§7.2).

Two jobs, and the first one is the whole reason this module is separate from
the service.

**The client view is built from an allow-list.** ``quote_versions.snapshot``
holds the trip *and* the internal costing: ``cost_subtotal``,
``profit_value``, ``supplier_paid_total``, the per-component cost breakdown.
A portal that returned the snapshot minus a few keys would be one forgotten
key away from showing a client the margin on their own holiday — and the key
that gets forgotten is always the one added later, by somebody working on
pricing who has never read this file. So nothing is removed; only named fields
are copied across, and a field the snapshot gains tomorrow does not appear
here until somebody adds it on purpose. This is §2's internal/client split
carried into §7: a boundary that holds because of what the code *cannot* do,
not because of what it remembers not to do.

**Whether a grant still works, and what to say when it does not.** Expired,
revoked, or a booking that has been cancelled — three different sentences,
because "this link no longer works" sends a client to the telephone with
nothing to say, and a client who has cancelled and is being told their link
expired will (rightly) assume the system has lost their booking.

Nothing here re-prices anything. The client sees the figures frozen into the
version they accepted (§3.4), for the reason §7.1 books against the version:
what they agreed to is not what a re-run of the engine would say today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

#: Why a link does not work. Distinct codes because the three call for three
#: different things from whoever reads them.
EXPIRED = "portal_link_expired"
REVOKED = "portal_link_revoked"
CANCELLED = "portal_booking_cancelled"


class AccessRefused(Exception):
    """A grant that will not open a trip, with something a client can act on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Grant:
    """A grant as the rules see it. Not the ORM row."""

    expires_on: date
    revoked: bool = False
    revoke_reason: str | None = None
    booking_status: str = "provisional"
    booking_reference: str = ""


def check_access(grant: Grant, *, today: date) -> None:
    """Whether this grant may open its booking.

    Order matters: a cancelled booking is reported as cancelled even where the
    link has also expired, because that is the fact the client needs and the
    expiry is then beside the point.
    """
    if grant.booking_status == "cancelled":
        raise AccessRefused(
            CANCELLED,
            f"Booking {grant.booking_reference} has been cancelled, so there "
            f"is no trip to show. If that is not right, your consultant can "
            f"tell you what happened — the record of it is not gone.",
        )
    if grant.revoked:
        raise AccessRefused(
            REVOKED,
            "This link has been withdrawn. Ask your consultant for a new one; "
            "your booking is unaffected.",
        )
    if grant.expires_on < today:
        raise AccessRefused(
            EXPIRED,
            f"This link stopped working on {grant.expires_on:%d %B %Y}. Your "
            f"consultant can send a fresh one — nothing about your booking has "
            f"changed.",
        )


def default_expiry(
    departure: date, *, after_days: int, today: date, minimum_days: int = 30
) -> date:
    """The last day a link should work, given when they travel.

    Departure plus a margin, not departure: the statement, the receipts and the
    itinerary are all wanted after the trip, and a link that dies on the day
    they fly home is a support call rather than a security measure.

    Floored at ``minimum_days`` from today, which is the case that would
    otherwise bite — a booking made for a trip that has already happened (a
    late record, an import) would otherwise get a link that was dead on
    arrival.
    """
    return max(
        departure + _days(after_days),
        today + _days(minimum_days),
    )


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


# --------------------------------------------------------------------------- #
# The trip, as a client may see it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Movement:
    label: str
    minutes: int | None = None


@dataclass(frozen=True)
class Day:
    """One day of the programme, as it was quoted."""

    number: int
    #: ``on`` rather than ``date``: it is what §4.1's own ``Day`` calls it, and
    #: a field named for its type shadows the type in a schema that reads it.
    on: date | None
    destination: str = ""
    property_name: str = ""
    board: str = ""
    movements: list[Movement] = field(default_factory=list)
    excursions: list[str] = field(default_factory=list)
    is_arrival: bool = False
    is_departure: bool = False
    has_night: bool = True


@dataclass(frozen=True)
class Stay:
    """One property on the trip, and how long they are there."""

    sequence: int
    destination: str = ""
    property_name: str = ""
    room_type: str = ""
    board: str = ""
    rooms: int = 0
    nights: int = 0


@dataclass(frozen=True)
class Trip:
    """What the client booked. No cost, no margin, and no other option.

    Only the option they accepted: a quote offers three to nine (§3.7), and
    showing a client the two they turned down re-opens a decision they have
    already made and paid a deposit on.
    """

    reference: str = ""
    status: str = ""
    arrival_date: date | None = None
    departure_date: date | None = None
    pax_count: int = 0
    total: Decimal = Decimal(0)
    currency: str = ""
    property_name: str = ""
    room_type: str = ""
    board: str = ""
    nights: int = 0
    description: str | None = None
    stays: list[Stay] = field(default_factory=list)
    days: list[Day] = field(default_factory=list)
    included: list[str] = field(default_factory=list)


#: The only keys copied out of a snapshot option. An allow-list, and the
#: comment above says why: the snapshot's other keys are the costing.
_OPTION_FIELDS = (
    "accommodation_name",
    "room_type_name",
    "meal_plan_name",
    "nights",
    "blurb",
    "activities",
    "legs",
    "days",
)


def option_of(snapshot: dict[str, Any], option_id: str | None) -> dict[str, Any]:
    """The booked option out of a frozen snapshot.

    Falls back to the recommended one, then to the first. A booking made before
    an option was selected has no ``option_id``, and a trip page that showed
    nothing in that case would be worse than one showing the package the client
    was steered towards.
    """
    options = [one for one in snapshot.get("options", []) if isinstance(one, dict)]
    if not options:
        return {}
    if option_id:
        found = next(
            (one for one in options if str(one.get("option_id")) == str(option_id)),
            None,
        )
        if found is not None:
            return found
    recommended = next(
        (one for one in options if one.get("is_recommended")), None
    )
    return recommended or options[0]


def trip_of(
    snapshot: dict[str, Any],
    *,
    option_id: str | None,
    reference: str,
    status: str,
    arrival: date | None,
    departure: date | None,
    pax_count: int,
    total: Decimal,
    currency: str,
) -> Trip:
    """A client-safe trip from a frozen version snapshot.

    The money comes from the **booking**, not from the snapshot: §7.1 froze the
    figure onto the booking precisely so that a re-priced quote cannot move
    what the client owes, and reading it back off the snapshot here would
    quietly undo that.
    """
    option = option_of(snapshot, option_id)
    picked = {key: option.get(key) for key in _OPTION_FIELDS}
    return Trip(
        reference=reference,
        status=status,
        arrival_date=arrival,
        departure_date=departure,
        pax_count=pax_count,
        total=total,
        currency=(currency or "").upper(),
        property_name=str(picked.get("accommodation_name") or ""),
        room_type=str(picked.get("room_type_name") or ""),
        board=str(picked.get("meal_plan_name") or ""),
        nights=int(picked.get("nights") or 0),
        description=(
            str(picked["blurb"]) if picked.get("blurb") else None
        ),
        stays=_stays(picked.get("legs")),
        days=_days_of(picked.get("days")),
        included=[str(one) for one in (picked.get("activities") or [])],
    )


def _stays(legs: Any) -> list[Stay]:
    if not isinstance(legs, list):
        return []
    out: list[Stay] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        out.append(
            Stay(
                sequence=int(leg.get("sequence") or 0),
                destination=str(leg.get("destination_name") or ""),
                property_name=str(leg.get("accommodation_name") or ""),
                room_type=str(leg.get("room_type_name") or ""),
                board=str(leg.get("meal_plan_name") or ""),
                rooms=int(leg.get("rooms_required") or 0),
                nights=int(leg.get("nights") or 0),
            )
        )
    return sorted(out, key=lambda one: one.sequence)


def _days_of(days: Any) -> list[Day]:
    if not isinstance(days, list):
        return []
    out: list[Day] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        out.append(
            Day(
                number=int(day.get("number") or 0),
                on=_date(day.get("date")),
                destination=str(day.get("destination") or ""),
                property_name=str(day.get("property_name") or ""),
                board=str(day.get("board") or ""),
                movements=_movements(day.get("movements")),
                excursions=[str(one) for one in (day.get("excursions") or [])],
                is_arrival=bool(day.get("is_arrival")),
                is_departure=bool(day.get("is_departure")),
                has_night=bool(day.get("has_night", True)),
            )
        )
    return sorted(out, key=lambda one: one.number)


def _movements(movements: Any) -> list[Movement]:
    """Movements, reading both shapes a snapshot can hold.

    Versions frozen before §4.2 hold plain strings; later ones hold a label and
    a duration. The document reads both (§4.2) and so must this, because an
    itinerary issued in August is still the itinerary that client is travelling
    on.
    """
    if not isinstance(movements, list):
        return []
    out: list[Movement] = []
    for one in movements:
        if isinstance(one, str):
            out.append(Movement(label=one))
        elif isinstance(one, dict):
            minutes = one.get("minutes")
            out.append(
                Movement(
                    label=str(one.get("label") or ""),
                    minutes=int(minutes) if minutes is not None else None,
                )
            )
    return out


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "CANCELLED",
    "EXPIRED",
    "REVOKED",
    "AccessRefused",
    "Day",
    "Grant",
    "Movement",
    "Stay",
    "Trip",
    "check_access",
    "default_expiry",
    "option_of",
    "trip_of",
]
