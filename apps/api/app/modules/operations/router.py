"""Crew, assignments and the departure board over the API (§8.1)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.operations.schemas import (
    ActualRead,
    AssignmentCreate,
    AssignmentMade,
    AssignmentRead,
    ClashRead,
    CrewCreate,
    CrewRead,
    CrewUpdate,
    DepartureRead,
    FindingRead,
    FleetTruthRead,
    FuelFillCreate,
    FuelFillRead,
    GapRead,
    RosterRead,
    TripLogClose,
    TripLogOpen,
    TripLogOpened,
    TripLogRead,
)
from app.modules.operations.service import (
    AssignmentService,
    CrewService,
    TripLogService,
)
from app.modules.users.models import User

router = APIRouter(tags=["operations"])

CREW_READ = "crew:read"
CREW_MANAGE = "crew:manage"
ASSIGN_READ = "assignment:read"
ASSIGN_MANAGE = "assignment:manage"
#: Its own pair. What a vehicle actually burned is the evidence every future
#: transport price rests on, and a fuel receipt is money leaving the business.
LOG_READ = "fleet_log:read"
LOG_MANAGE = "fleet_log:manage"


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


# --------------------------------------------------------------------------- #
# What the vehicle actually did (§8.2)
# --------------------------------------------------------------------------- #


@router.post(
    "/assignments/{assignment_id}/log",
    response_model=TripLogOpened,
    status_code=201,
)
async def open_log(
    assignment_id: uuid.UUID,
    body: TripLogOpen,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(LOG_MANAGE)),
):
    """Record a vehicle leaving the yard.

    Opened and closed rather than written in one go: the vehicle leaves on
    Monday and comes back on Friday, and the days in between are exactly when
    somebody wants to know where it is.

    Any kilometres between this reading and the vehicle's last return come back
    as an observation. That gap is not an error — repositioning, a service run
    — but it is the one thing an odometer is uniquely good at seeing, and a
    response that said only "created" would bury it.
    """
    log, observations = await TripLogService(db).open(
        assignment_id, actor_id=actor.id, **body.model_dump(exclude_unset=True)
    )
    return TripLogOpened(
        log=_log(log), observations=observations
    )


@router.post("/trip-logs/{log_id}/close", response_model=TripLogRead)
async def close_log(
    log_id: uuid.UUID,
    body: TripLogClose,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(LOG_MANAGE)),
):
    """Record a vehicle coming back.

    A closing reading below the opening one is refused rather than stored: an
    odometer does not run backwards, so it is a typed digit — and a negative
    distance in a fleet average poisons every figure derived from it.
    """
    return _log(
        await TripLogService(db).close(
            log_id,
            odometer_in_km=body.odometer_in_km,
            ended_on=body.ended_on,
        )
    )


@router.post(
    "/trip-logs/{log_id}/fuel", response_model=FuelFillRead, status_code=201
)
async def add_fuel(
    log_id: uuid.UUID,
    body: FuelFillCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(LOG_MANAGE)),
):
    """Record a fuel receipt against a trip.

    Litres and money, both off the paper, because neither can be recovered
    from the other once it is gone.
    """
    return await TripLogService(db).fill(
        log_id, actor_id=actor.id, **body.model_dump(exclude_unset=True)
    )


@router.get("/trip-logs/{log_id}/actual", response_model=ActualRead)
async def trip_actual(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(LOG_READ)),
):
    """What this trip measured, beside what the pricing model predicted.

    The first time in this platform's life that a transport figure has been
    checkable against a receipt.
    """
    service = TripLogService(db)
    actual = await service.measured(await service.get(log_id))
    return ActualRead(
        distance_km=actual.distance_km,
        litres=actual.litres,
        fuel_cost=actual.fuel_cost,
        currency=actual.currency,
        model_kmpl=actual.model_kmpl,
        actual_kmpl=actual.actual_kmpl,
        model_litres=actual.model_litres,
        variance_pct=actual.variance_pct,
    )


@router.get("/bookings/{booking_id}/trip-logs", response_model=list[TripLogRead])
async def booking_logs(
    booking_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(LOG_READ)),
):
    """Every vehicle's log on this booking.

    One per vehicle, not one per booking: a group in two Land Cruisers is two
    odometers and two sets of receipts, and pooling them would lose exactly the
    comparison worth making.
    """
    return [_log(one) for one in await TripLogService(db).for_booking(booking_id)]


@router.get("/operations/fuel-audit", response_model=list[FleetTruthRead])
async def fuel_audit(
    since: date | None = Query(
        default=None, description="Only trips starting on or after this day."
    ),
    vehicle_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(LOG_READ)),
):
    """Whether the fuel figure every quote is priced on is true.

    §2.5 put ``fuel_consumption_kmpl`` on a vehicle and every transport line
    since has been computed from it. Nothing could ever disprove it — which is
    a different and quieter failure than a missing column, because a vehicle
    priced at 8.5 km/L that really does 6.9 under-costs every safari by a fifth
    for as long as nobody measures.

    This **reports**. It does not change the figure: that is a live pricing
    input, moving it re-prices work in flight, and deciding a fortnight of
    receipts is the new truth belongs to whoever will have to explain the
    margin. Under three measured trips it says so rather than concluding from
    two.
    """
    service = TripLogService(db)
    found = (
        [await service.audit_vehicle(vehicle_id, since=since)]
        if vehicle_id
        else await service.audit_fleet(since=since)
    )
    return [
        FleetTruthRead(
            vehicle=one.vehicle,
            trips=one.trips,
            distance_km=one.distance_km,
            litres=one.litres,
            fuel_cost=one.fuel_cost,
            currency=one.currency,
            model_kmpl=one.model_kmpl,
            actual_kmpl=one.actual_kmpl,
            findings=[FindingRead(**vars(f)) for f in one.findings],
        )
        for one in found
    ]


def _log(row) -> TripLogRead:
    return TripLogRead(
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "assignment_id",
                "vehicle_id",
                "booking_id",
                "odometer_out_km",
                "odometer_in_km",
                "started_on",
                "ended_on",
                "driver_id",
                "notes",
            )
        },
        distance_km=row.distance_km,
        is_open=row.is_open,
        fills=[FuelFillRead.model_validate(one) for one in row.fills],
    )
