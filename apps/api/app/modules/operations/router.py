"""Crew, assignments and the departure board over the API (§8.1)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.operations.schemas import (
    AssignmentCreate,
    AssignmentMade,
    AssignmentRead,
    ClashRead,
    CrewCreate,
    CrewRead,
    CrewUpdate,
    DepartureRead,
    GapRead,
    RosterRead,
)
from app.modules.operations.service import AssignmentService, CrewService
from app.modules.users.models import User

router = APIRouter(tags=["operations"])

CREW_READ = "crew:read"
CREW_MANAGE = "crew:manage"
ASSIGN_READ = "assignment:read"
ASSIGN_MANAGE = "assignment:manage"


# --------------------------------------------------------------------------- #
# The register of drivers and guides
# --------------------------------------------------------------------------- #


@router.post("/crew", response_model=CrewRead, status_code=201)
async def create_crew(
    body: CrewCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(CREW_MANAGE)),
):
    """Add a driver, a guide, or — usually — both.

    One record whatever they do. A driver-guide split across two rows would be
    assigned twice, double-booked against themselves, and counted twice on a
    cost sheet.
    """
    return await CrewService(db).create(body.model_dump(exclude_unset=True))


@router.get("/crew", response_model=list[CrewRead])
async def list_crew(
    role: str | None = Query(
        default=None, description="driver | guide | driver_guide"
    ),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(CREW_READ)),
):
    """The roster. Filtering by ``driver`` includes driver-guides, as it must."""
    return await CrewService(db).list(role=role, active_only=active_only)


@router.get("/crew/{crew_id}", response_model=CrewRead)
async def get_crew(
    crew_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(CREW_READ)),
):
    return await CrewService(db).get(crew_id)


@router.patch("/crew/{crew_id}", response_model=CrewRead)
async def update_crew(
    crew_id: uuid.UUID,
    body: CrewUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(CREW_MANAGE)),
):
    """Correct a record — a renewed licence, a new number, a role added.

    Deactivating somebody here does not strip them off trips they are already
    on: those are commitments an operator has made, and quietly emptying a trip
    sheet is worse than a person who has to be taken off it deliberately.
    """
    return await CrewService(db).update(
        crew_id, body.model_dump(exclude_unset=True)
    )


# --------------------------------------------------------------------------- #
# What is committed to a trip
# --------------------------------------------------------------------------- #


@router.post(
    "/bookings/{booking_id}/assignments",
    response_model=AssignmentMade,
    status_code=201,
)
async def assign(
    booking_id: uuid.UUID,
    body: AssignmentCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(ASSIGN_MANAGE)),
):
    """Put a vehicle or a person on a booking.

    A **clash is refused**: two trips over the same days is one trip that does
    not happen. A same-day handover is not a clash — a vehicle dropping a group
    at the airport in the morning and collecting another that afternoon is a
    normal Tuesday at the coast — but it comes back as an advisory, because a
    tight one and a comfortable one look identical otherwise.

    An operator who knows the first booking is about to be cancelled can push
    past a real clash with ``override_reason``, which stays on the row with
    their name. The point is not to make it impossible; it is to make it
    attributable.
    """
    assignment, advisories = await AssignmentService(db).assign(
        booking_id, actor_id=actor.id, **body.model_dump(exclude_unset=True)
    )
    return AssignmentMade(
        assignment=AssignmentRead.model_validate(assignment),
        advisories=[ClashRead(**vars(one)) for one in advisories],
    )


@router.get(
    "/bookings/{booking_id}/assignments", response_model=list[AssignmentRead]
)
async def booking_assignments(
    booking_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(ASSIGN_READ)),
):
    return await AssignmentService(db).for_booking(booking_id)


@router.get("/bookings/{booking_id}/readiness", response_model=list[GapRead])
async def readiness(
    booking_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(ASSIGN_READ)),
):
    """What still stands between this booking and a trip that can leave.

    An empty list is the answer. A missing **guide** is deliberately not
    reported: whether a trip needs one depends on whether the client asked and
    paid for one, and a board that complained about every self-drive booking is
    a board nobody opens.
    """
    return await AssignmentService(db).readiness(booking_id)


@router.delete("/assignments/{assignment_id}", status_code=204)
async def release(
    assignment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(ASSIGN_MANAGE)),
):
    """Take something off a trip.

    A real delete, unlike §5.3's contact log: an assignment is a *plan*, not a
    record of something that happened, and keeping every vehicle ever pencilled
    in would make "what is out on the 5th" a query that has to exclude ghosts.
    """
    await AssignmentService(db).release(assignment_id)


# --------------------------------------------------------------------------- #
# The two lists an operator actually opens
# --------------------------------------------------------------------------- #


@router.get("/operations/departures", response_model=list[DepartureRead])
async def departures(
    days: int = Query(default=14, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(ASSIGN_READ)),
):
    """Everything leaving soon, and what it is missing. Soonest first.

    The operational half of §5.2's morning list, and the reason §8.1 exists:
    until now a confirmed booking led nowhere — operations found out what was
    travelling by reading the bookings screen and keeping a separate
    spreadsheet of who was driving.
    """
    found = await AssignmentService(db).departures(days=days)
    return [
        DepartureRead(
            booking_id=booking.id,
            reference=booking.reference,
            arrival_date=booking.arrival_date,
            departure_date=booking.departure_date,
            pax_count=booking.pax_count,
            status=booking.status,
            roster=RosterRead(
                vehicles=[one.name for one in roster.vehicles],
                drivers=[one.name for one in roster.drivers],
                guides=[one.name for one in roster.guides],
                seats=roster.seats,
            ),
            gaps=[GapRead(**vars(one)) for one in gaps],
        )
        for booking, roster, gaps in found
    ]


@router.get("/operations/diary", response_model=list[AssignmentRead])
async def diary(
    starts_on: date = Query(description="First day of the span."),
    ends_on: date = Query(description="Last day of the span, inclusive."),
    vehicle_id: uuid.UUID | None = Query(default=None),
    crew_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(ASSIGN_READ)),
):
    """What is committed over a span — the fleet and crew calendar.

    The query an operator makes before promising anything, because "what have I
    got free that week" is really "what is already out".
    """
    return await AssignmentService(db).diary(
        starts_on=starts_on,
        ends_on=ends_on,
        vehicle_id=vehicle_id,
        crew_id=crew_id,
    )
