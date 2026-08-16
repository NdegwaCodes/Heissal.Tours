from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.activities.models import Activity, ActivityRate
from app.modules.activities.schemas import (
    ActivityCreate,
    ActivityRateCreate,
    ActivityRateRead,
    ActivityRead,
    ActivityUpdate,
)
from app.modules.activities.service import ActivityRateService

router = APIRouter(tags=["activities"])

READ = "activity:read"
MANAGE = "activity:manage"


@router.get("/activities", response_model=list[ActivityRead])
async def list_activities(
    destination_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    stmt = select(Activity)
    if destination_id:
        stmt = stmt.where(Activity.destination_id == destination_id)
    return (await db.execute(stmt.limit(200))).scalars().all()


@router.post("/activities", response_model=ActivityRead, status_code=201)
async def create_activity(
    body: ActivityCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    data = body.model_dump()
    data["slug"] = slugify(data.get("slug") or data["name"])
    return await CRUDService(db, Activity).create(data)


@router.get("/activities/{activity_id}", response_model=ActivityRead)
async def get_activity(
    activity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, Activity).get(activity_id)


@router.patch("/activities/{activity_id}", response_model=ActivityRead)
async def update_activity(
    activity_id: uuid.UUID,
    body: ActivityUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    return await CRUDService(db, Activity).update(
        activity_id, body.model_dump(exclude_unset=True)
    )


@router.get("/activities/{activity_id}/rates", response_model=list[ActivityRateRead])
async def list_rates(
    activity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rows = (
        await db.execute(
            select(ActivityRate)
            .where(ActivityRate.activity_id == activity_id)
            .order_by(ActivityRate.effective_from.desc())
        )
    ).scalars().all()
    return rows


@router.post("/activities/{activity_id}/rates", response_model=ActivityRateRead, status_code=201)
async def create_rate(
    activity_id: uuid.UUID,
    body: ActivityRateCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    if body.effective_to < body.effective_from:
        raise AppError("effective_to must be on or after effective_from.")
    await CRUDService(db, Activity).get(activity_id)  # 404 if missing
    data = body.model_dump()
    data["currency"] = body.currency.upper()
    rate = ActivityRate(activity_id=activity_id, **data)
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return rate


@router.get("/activities/{activity_id}/resolve-rate", response_model=ActivityRateRead)
async def resolve_rate(
    activity_id: uuid.UUID,
    residence_category_id: uuid.UUID,
    on_date: date,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await ActivityRateService(db).select_rate(
        activity_id=activity_id,
        residence_category_id=residence_category_id,
        on_date=on_date,
    )
