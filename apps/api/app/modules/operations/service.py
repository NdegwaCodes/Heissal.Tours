"""Assigning vehicles and crew, and the departure board (§8.1).

The rules are in :mod:`app.modules.operations.roster`; this is the half that
talks to the database. Four things worth reading before changing anything.

**A clash is refused, and an override is recorded.** Two trips over the same
days is one trip that does not happen, so the default is no. But an operator
who knows the first booking is about to be cancelled needs a way through, and
the way through leaves a reason and a name on the row — the same shape as
§5.3's amendment: not "can this be got round" but "who decided, and why".

**Only an active booking can be crewed.** A cancelled trip holds nothing, and
cancelling one releases what it held — otherwise the fleet calendar fills up
with vehicles nobody is using and an operator stops believing it.

**The departure board is one query, not one per booking.** It is the §5.2
lesson about the morning list: a list that takes four seconds to build is a
list nobody opens on a Monday.

**Nothing here decides a trip is ready.** It reports what is missing. Whether a
trip with no guide is a problem depends on whether the client asked for one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.bookings.models import ACTIVE_STATUSES, Booking
from app.modules.operations.models import VEHICLE, CrewMember, TripAssignment
from app.modules.operations.roster import (
    CREW_ROLES,
    DRIVES,
    GUIDES,
    AssignmentRefused,
    Clash,
    Crew,
    Fleet,
    Gap,
    Held,
    Roster,
    Window,
    check_crew,
    check_ready,
    check_vehicle,
    clashes,
    sort_gaps,
)
from app.modules.vehicles.models import Vehicle


def normalise_roles(values: Sequence[str] | None) -> list[str]:
    """Crew roles as the rules expect them, refusing anything else.

    Refused rather than dropped: a person saved with a typo'd role would be
    silently unassignable, and the failure would surface as "why can I not put
    Joseph on this trip" three weeks later.
    """
    cleaned = []
    for value in values or []:
        role = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if role not in CREW_ROLES:
            raise AppError(
                f"'{value}' is not something somebody does on a trip. Say "
                f"{', '.join(CREW_ROLES)}."
            )
        if role not in cleaned:
            cleaned.append(role)
    if not cleaned:
        raise AppError(
            "Say what this person does — driver, guide, or driver_guide. "
            "Somebody with no role cannot be put on a trip at all."
        )
    return cleaned


class CrewService:
    """The register of drivers and guides."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> CrewMember:
        data = dict(data)
        data["roles"] = normalise_roles(data.get("roles"))
        row = CrewMember(**data)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get(self, crew_id: uuid.UUID) -> CrewMember:
        row = await self.db.get(CrewMember, crew_id)
        if row is None:
            raise NotFoundError("Crew member not found.")
        return row

    async def list(
        self, *, role: str | None = None, active_only: bool = True
    ) -> list[CrewMember]:
        stmt = select(CrewMember).order_by(CrewMember.name)
        if active_only:
            stmt = stmt.where(CrewMember.is_active.is_(True))
        rows = list((await self.db.execute(stmt)).scalars().all())
        if role:
            wanted = DRIVES if role == "driver" else GUIDES if role == "guide" else (role,)
            rows = [one for one in rows if set(one.roles) & set(wanted)]
        return rows

    async def update(self, crew_id: uuid.UUID, data: dict) -> CrewMember:
        row = await self.get(crew_id)
        if "roles" in data and data["roles"] is not None:
            data["roles"] = normalise_roles(data["roles"])
        for field, value in data.items():
            setattr(row, field, value)
        await self.db.commit()
        await self.db.refresh(row)
        return row


class AssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- committing something to a trip --------------------------------------- #

    async def assign(
        self,
        booking_id: uuid.UUID,
        *,
        vehicle_id: uuid.UUID | None = None,
        crew_id: uuid.UUID | None = None,
        role: str | None = None,
        starts_on: date | None = None,
        ends_on: date | None = None,
        notes: str | None = None,
        override_reason: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> tuple[TripAssignment, list[Clash]]:
        """Put a vehicle or a person on a booking, and say what it clashed with.

        Returns the advisory clashes as well as the row: a same-day handover is
        not a refusal but it is worth an operator's eye, and swallowing it
        would make the tight one indistinguishable from the comfortable one.
        """
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        if booking.status not in ACTIVE_STATUSES:
            raise AppError(
                f"Booking {booking.reference} is {booking.status}, so there is "
                f"no trip to crew. Cancelling a booking releases what it held."
            )
        if (vehicle_id is None) == (crew_id is None):
            raise AppError(
                "An assignment is one vehicle or one person, not both and not "
                "neither."
            )

        window = Window(
            starts_on=starts_on or booking.arrival_date,
            ends_on=ends_on or booking.departure_date,
        )

        try:
            if vehicle_id is not None:
                found = await self._vehicle(vehicle_id)
                check_vehicle(found)
                subject, kind = found.name, VEHICLE
            else:
                assert crew_id is not None
                member = await self.get_crew(crew_id)
                kind = (role or "").strip().lower() or _default_role(member)
                check_crew(_crew(member), kind, window)
                subject = member.name
        except AssignmentRefused as exc:
            raise AppError(str(exc)) from exc

        held = await self._held(vehicle_id=vehicle_id, crew_id=crew_id)
        found_clashes = clashes(window, held, subject=subject)
        blocking = [one for one in found_clashes if one.blocking]
        if blocking and not (override_reason or "").strip():
            raise AppError(
                blocking[0].message
                + " Assign something else, or say why you are overriding it."
            )

        row = TripAssignment(
            booking_id=booking.id,
            vehicle_id=vehicle_id,
            crew_id=crew_id,
            role=kind,
            starts_on=window.starts_on,
            ends_on=window.ends_on,
            notes=notes,
            override_reason=(override_reason or "").strip() or None,
            assigned_by=actor_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row, [one for one in found_clashes if not one.blocking]

    async def release(self, assignment_id: uuid.UUID) -> None:
        """Take something off a trip.

        A hard delete, unlike §5.3's log. An assignment is a *plan*, not a
        record of something that happened — nobody needs the history of a
        vehicle that was pencilled in on Tuesday and swapped on Wednesday, and
        keeping it would make "what is out on the 5th" a query that has to
        exclude ghosts.
        """
        row = await self.db.get(TripAssignment, assignment_id)
        if row is None:
            raise NotFoundError("Assignment not found.")
        await self.db.delete(row)
        await self.db.commit()

    async def for_booking(self, booking_id: uuid.UUID) -> list[TripAssignment]:
        return list(
            (
                await self.db.execute(
                    select(TripAssignment)
                    .where(TripAssignment.booking_id == booking_id)
                    .order_by(TripAssignment.starts_on, TripAssignment.role)
                )
            )
            .scalars()
            .all()
        )

    # -- what is missing ------------------------------------------------------ #

    async def readiness(
        self, booking_id: uuid.UUID, *, today: date | None = None
    ) -> list[Gap]:
        """What still stands between this booking and a trip that can leave."""
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        rosters = await self._rosters([booking])
        return sort_gaps(
            check_ready(rosters[booking.id], today=today or date.today())
        )

    async def departures(
        self, *, days: int = 14, today: date | None = None
    ) -> list[tuple[Booking, Roster, list[Gap]]]:
        """Everything leaving in the next ``days``, and what it is missing.

        The operational half of §5.2's morning list. Built in a fixed number of
        queries however many bookings it covers, for the same reason: a list
        that takes four seconds is a list nobody opens on a Monday.
        """
        when = today or date.today()
        horizon = when.toordinal() + max(days, 0)
        bookings = list(
            (
                await self.db.execute(
                    select(Booking)
                    .where(
                        Booking.status.in_(ACTIVE_STATUSES),
                        Booking.arrival_date >= when,
                        Booking.arrival_date <= date.fromordinal(horizon),
                    )
                    .order_by(Booking.arrival_date)
                )
            )
            .scalars()
            .all()
        )
        rosters = await self._rosters(bookings)
        out = []
        for booking in bookings:
            roster = rosters[booking.id]
            out.append(
                (booking, roster, sort_gaps(check_ready(roster, today=when)))
            )
        return out

    async def diary(
        self,
        *,
        starts_on: date,
        ends_on: date,
        vehicle_id: uuid.UUID | None = None,
        crew_id: uuid.UUID | None = None,
    ) -> list[TripAssignment]:
        """What is committed over a span — the fleet and crew calendar.

        The query an operator makes before promising anything: "what have I got
        free that week?" is really "what is already out".
        """
        window = Window(starts_on=starts_on, ends_on=ends_on)
        stmt = select(TripAssignment).where(
            TripAssignment.starts_on <= window.ends_on,
            TripAssignment.ends_on >= window.starts_on,
        )
        if vehicle_id is not None:
            stmt = stmt.where(TripAssignment.vehicle_id == vehicle_id)
        if crew_id is not None:
            stmt = stmt.where(TripAssignment.crew_id == crew_id)
        return list(
            (
                await self.db.execute(
                    stmt.order_by(TripAssignment.starts_on, TripAssignment.role)
                )
            )
            .scalars()
            .all()
        )

    # -- plumbing ------------------------------------------------------------- #

    async def get_crew(self, crew_id: uuid.UUID) -> CrewMember:
        row = await self.db.get(CrewMember, crew_id)
        if row is None:
            raise NotFoundError("Crew member not found.")
        return row

    async def _vehicle(self, vehicle_id: uuid.UUID) -> Fleet:
        row = await self.db.get(Vehicle, vehicle_id)
        if row is None:
            raise NotFoundError("Vehicle not found.")
        return Fleet(
            name=row.name,
            vehicle_type=row.vehicle_type,
            passenger_capacity=row.passenger_capacity,
            is_active=row.is_active,
        )

    async def _held(
        self, *, vehicle_id: uuid.UUID | None, crew_id: uuid.UUID | None
    ) -> list[Held]:
        """Everything this vehicle or person is already committed to.

        Only on **active** bookings: a cancelled trip holds nothing, and a
        fleet calendar that says otherwise fills up with vehicles nobody is
        using until an operator stops believing it.
        """
        stmt = (
            select(TripAssignment, Booking.reference)
            .join(Booking, Booking.id == TripAssignment.booking_id)
            .where(Booking.status.in_(ACTIVE_STATUSES))
        )
        if vehicle_id is not None:
            stmt = stmt.where(TripAssignment.vehicle_id == vehicle_id)
        else:
            stmt = stmt.where(TripAssignment.crew_id == crew_id)
        return [
            Held(
                window=Window(starts_on=row.starts_on, ends_on=row.ends_on),
                reference=reference,
                booking_id=row.booking_id,
            )
            for row, reference in (await self.db.execute(stmt)).all()
        ]

    async def _rosters(
        self, bookings: Sequence[Booking]
    ) -> dict[uuid.UUID, Roster]:
        """Who and what is on each of these bookings, in three queries."""
        ids = [booking.id for booking in bookings]
        out = {
            booking.id: Roster(
                reference=booking.reference,
                departs_on=booking.arrival_date,
                pax_count=booking.pax_count,
            )
            for booking in bookings
        }
        if not ids:
            return out

        rows = list(
            (
                await self.db.execute(
                    select(TripAssignment).where(
                        TripAssignment.booking_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return out

        vehicles = {
            row.id: row
            for row in (
                await self.db.execute(
                    select(Vehicle).where(
                        Vehicle.id.in_(
                            [one.vehicle_id for one in rows if one.vehicle_id]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        crew = {
            row.id: row
            for row in (
                await self.db.execute(
                    select(CrewMember).where(
                        CrewMember.id.in_(
                            [one.crew_id for one in rows if one.crew_id]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }

        for row in rows:
            roster = out.get(row.booking_id)
            if roster is None:
                continue
            if row.vehicle_id is not None:
                found = vehicles.get(row.vehicle_id)
                if found is not None:
                    roster.vehicles.append(
                        Fleet(
                            name=found.name,
                            vehicle_type=found.vehicle_type,
                            passenger_capacity=found.passenger_capacity,
                            is_active=found.is_active,
                        )
                    )
            elif row.crew_id is not None:
                member = crew.get(row.crew_id)
                if member is None:
                    continue
                # By the role on the **assignment**, not by what the person is
                # capable of: a driver-guide sent out purely to guide is not a
                # driver on this trip, and counting them as one would report a
                # trip with nobody at the wheel as ready.
                if row.role in DRIVES:
                    roster.drivers.append(_crew(member))
                if row.role in GUIDES:
                    roster.guides.append(_crew(member))
        return out


def _crew(member: CrewMember) -> Crew:
    return Crew(
        name=member.name,
        roles=tuple(member.roles or ()),
        is_active=member.is_active,
        licence_expires_on=member.licence_expires_on,
    )


def _default_role(member: CrewMember) -> str:
    """The role to assume where the caller did not say.

    Their single role where they have one, and a refusal where they have
    several: putting a driver-guide down as one or the other by coin-flip is
    how a trip sheet ends up saying something nobody meant.
    """
    roles = list(member.roles or ())
    if len(roles) == 1:
        return roles[0]
    raise AssignmentRefused(
        f"{member.name} is down as {', '.join(roles)}. Say which of those they "
        f"are doing on this trip — a trip sheet has to name one."
    )


__all__ = [
    "AssignmentService",
    "CrewService",
    "normalise_roles",
]
