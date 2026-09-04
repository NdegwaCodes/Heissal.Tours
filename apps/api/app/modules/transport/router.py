"""Routes and transport tariffs over the API (§4.2).

The route table is hand-entered by the operations team, so it needs a real
endpoint rather than a seeder. The two tariff tables get one for a plainer
reason: readiness has been telling operators to "load the fare before issuing"
since §3.10, and until now the only way to load one was to edit a seed script.
A blocking message whose fix needs a developer is not a fix.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService
from app.core.deps import require_permission
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.destinations.models import Destination
from app.modules.quotes.routing import normalise_types
from app.modules.transport.models import (
    COST_BASES,
    TRANSPORT_MODES,
    DestinationTransportMode,
    Route,
    TransferRate,
)
from app.modules.transport.schemas import (
    RouteCreate,
    RouteRead,
    RouteUpdate,
    TransferRateCreate,
    TransferRateRead,
    TransportModeCreate,
    TransportModeRead,
)

router = APIRouter(tags=["transport"])

READ = "route:read"
MANAGE = "route:manage"
TARIFF_READ = "transport_tariff:read"
TARIFF_MANAGE = "transport_tariff:manage"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/routes", response_model=list[RouteRead])
async def list_routes(
    destination_id: uuid.UUID | None = Query(
        default=None,
        description="Every route touching this place, in either direction.",
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    stmt = select(Route)
    if destination_id:
        # Either end: a route is a pair, and an operator looking up "the Mara"
        # means every road to and from it.
        stmt = stmt.where(
            or_(
                Route.origin_id == destination_id,
                Route.destination_id == destination_id,
            )
        )
    stmt = stmt.order_by(Route.effective_from.desc()).limit(500)
    return (await db.execute(stmt)).scalars().all()


@router.post("/routes", response_model=RouteRead, status_code=201)
async def create_route(
    body: RouteCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    for place in (body.origin_id, body.destination_id):
        await CRUDService(db, Destination).get(place)  # 404 if missing
    data = body.model_dump()
    data["required_vehicle_types"] = list(
        normalise_types(data.get("required_vehicle_types"))
    )
    route = Route(**data)
    db.add(route)
    await db.commit()
    await db.refresh(route)
    return route


@router.get("/routes/{route_id}", response_model=RouteRead)
async def get_route(
    route_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, Route).get(route_id)


@router.patch("/routes/{route_id}", response_model=RouteRead)
async def update_route(
    route_id: uuid.UUID,
    body: RouteUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    route = await CRUDService(db, Route).get(route_id)
    data = body.model_dump(exclude_unset=True)
    if "required_vehicle_types" in data:
        data["required_vehicle_types"] = list(
            normalise_types(data["required_vehicle_types"])
        )
    starts = data.get("effective_from", route.effective_from)
    ends = data.get("effective_to", route.effective_to)
    if ends is not None and ends < starts:
        raise AppError("effective_to must be on or after effective_from.")
    for field, value in data.items():
        setattr(route, field, value)
    await db.commit()
    await db.refresh(route)
    return route


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    route = await CRUDService(db, Route).get(route_id)
    await db.delete(route)
    await db.commit()


# --------------------------------------------------------------------------- #
# Transfer tariffs and line-haul fares
# --------------------------------------------------------------------------- #


@router.get(
    "/destinations/{destination_id}/transfer-rates",
    response_model=list[TransferRateRead],
)
async def list_transfer_rates(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_READ)),
):
    return (
        (
            await db.execute(
                select(TransferRate)
                .where(TransferRate.destination_id == destination_id)
                .order_by(TransferRate.effective_from.desc())
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/destinations/{destination_id}/transfer-rates",
    response_model=TransferRateRead,
    status_code=201,
)
async def create_transfer_rate(
    destination_id: uuid.UUID,
    body: TransferRateCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_MANAGE)),
):
    await CRUDService(db, Destination).get(destination_id)
    _check_window(body.effective_from, body.effective_to)
    data = body.model_dump()
    data["currency"] = data["currency"].upper()
    rate = TransferRate(destination_id=destination_id, **data)
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return rate


@router.delete("/transfer-rates/{rate_id}", status_code=204)
async def delete_transfer_rate(
    rate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_MANAGE)),
):
    rate = await CRUDService(db, TransferRate).get(rate_id)
    await db.delete(rate)
    await db.commit()


@router.get(
    "/destinations/{destination_id}/transport-modes",
    response_model=list[TransportModeRead],
)
async def list_transport_modes(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_READ)),
):
    return (
        (
            await db.execute(
                select(DestinationTransportMode)
                .where(DestinationTransportMode.destination_id == destination_id)
                .order_by(DestinationTransportMode.effective_from.desc())
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/destinations/{destination_id}/transport-modes",
    response_model=TransportModeRead,
    status_code=201,
)
async def create_transport_mode(
    destination_id: uuid.UUID,
    body: TransportModeCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_MANAGE)),
):
    await CRUDService(db, Destination).get(destination_id)
    _check_window(body.effective_from, body.effective_to)
    mode = body.mode.strip().lower()
    if mode not in TRANSPORT_MODES:
        # Air is absent on purpose and the message says why rather than listing
        # the permitted values: we hold no ticketing licence, so a flight is
        # named on the itinerary and never priced (§3.10).
        raise AppError(
            f"'{mode}' is not a mode we can sell a fare for. Priced modes are "
            f"{', '.join(TRANSPORT_MODES)} — flights are named on the itinerary "
            f"and their tickets are the client's to buy."
        )
    if body.cost_basis not in COST_BASES:
        raise AppError(
            f"cost_basis must be one of {', '.join(COST_BASES)}."
        )
    data = body.model_dump()
    data["mode"] = mode
    data["currency"] = data["currency"].upper()
    row = DestinationTransportMode(destination_id=destination_id, **data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/transport-modes/{mode_id}", status_code=204)
async def delete_transport_mode(
    mode_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(TARIFF_MANAGE)),
):
    row = await CRUDService(db, DestinationTransportMode).get(mode_id)
    await db.delete(row)
    await db.commit()


def _check_window(starts: date, ends: date | None) -> None:
    if ends is not None and ends < starts:
        raise AppError("effective_to must be on or after effective_from.")
