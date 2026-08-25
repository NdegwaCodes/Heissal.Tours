from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.accommodations.schemas import (
    AccommodationCreate,
    AccommodationRateCreate,
    AccommodationRateRead,
    AccommodationRead,
    AccommodationUpdate,
    MealPlanCreate,
    MealPlanRead,
    RoomTypeCreate,
    RoomTypeRead,
)
from app.modules.accommodations.service import AccommodationRateService

router = APIRouter(tags=["accommodations"])

READ = "accommodation:read"
MANAGE = "accommodation:manage"


# --- Meal plans ---

@router.get("/meal-plans", response_model=list[MealPlanRead])
async def list_meal_plans(db: AsyncSession = Depends(get_db), _=Depends(require_permission(READ))):
    return await CRUDService(db, MealPlan).list()


@router.post("/meal-plans", response_model=MealPlanRead, status_code=201)
async def create_meal_plan(
    body: MealPlanCreate, db: AsyncSession = Depends(get_db), _=Depends(require_permission(MANAGE))
):
    data = body.model_dump()
    data["code"] = data["code"].upper()
    return await CRUDService(db, MealPlan).create(data)


# --- Accommodations ---

@router.get("/accommodations", response_model=list[AccommodationRead])
async def list_accommodations(
    destination_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    stmt = select(Accommodation)
    if destination_id:
        stmt = stmt.where(Accommodation.destination_id == destination_id)
    return (await db.execute(stmt.limit(200))).scalars().all()


@router.post("/accommodations", response_model=AccommodationRead, status_code=201)
async def create_accommodation(
    body: AccommodationCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    data = body.model_dump()
    data["slug"] = slugify(data.get("slug") or data["name"])
    return await CRUDService(db, Accommodation).create(data)


@router.get("/accommodations/{accommodation_id}", response_model=AccommodationRead)
async def get_accommodation(
    accommodation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, Accommodation).get(accommodation_id)


@router.patch("/accommodations/{accommodation_id}", response_model=AccommodationRead)
async def update_accommodation(
    accommodation_id: uuid.UUID,
    body: AccommodationUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    return await CRUDService(db, Accommodation).update(
        accommodation_id, body.model_dump(exclude_unset=True)
    )


# --- Room types ---

@router.get("/accommodations/{accommodation_id}/room-types", response_model=list[RoomTypeRead])
async def list_room_types(
    accommodation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rows = (
        await db.execute(select(RoomType).where(RoomType.accommodation_id == accommodation_id))
    ).scalars().all()
    return rows


@router.post(
    "/accommodations/{accommodation_id}/room-types",
    response_model=RoomTypeRead,
    status_code=201,
)
async def create_room_type(
    accommodation_id: uuid.UUID,
    body: RoomTypeCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    await CRUDService(db, Accommodation).get(accommodation_id)  # 404 if missing
    room = RoomType(accommodation_id=accommodation_id, **body.model_dump())
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return room


# --- Rates ---

@router.get(
    "/accommodations/{accommodation_id}/rates", response_model=list[AccommodationRateRead]
)
async def list_rates(
    accommodation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rows = (
        await db.execute(
            select(AccommodationRate)
            .where(AccommodationRate.accommodation_id == accommodation_id)
            .order_by(AccommodationRate.effective_from.desc())
        )
    ).scalars().all()
    return rows


@router.post(
    "/accommodations/{accommodation_id}/rates",
    response_model=AccommodationRateRead,
    status_code=201,
)
async def create_rate(
    accommodation_id: uuid.UUID,
    body: AccommodationRateCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    await CRUDService(db, Accommodation).get(accommodation_id)  # 404 if missing
    return await AccommodationRateService(db).create_rate(
        accommodation_id, body.model_dump()
    )


@router.get(
    "/accommodations/{accommodation_id}/resolve-rate", response_model=AccommodationRateRead
)
async def resolve_rate(
    accommodation_id: uuid.UUID,
    room_type_id: uuid.UUID,
    meal_plan_id: uuid.UUID,
    residence_category_id: uuid.UUID,
    stay_date: date,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rate = await AccommodationRateService(db).select_rate(
        room_type_id=room_type_id,
        meal_plan_id=meal_plan_id,
        residence_category_id=residence_category_id,
        stay_date=stay_date,
    )
    if rate.accommodation_id != accommodation_id:
        raise NotFoundError("Rate does not belong to this accommodation.")
    return rate
