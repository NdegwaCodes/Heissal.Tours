from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService
from app.core.deps import require_permission
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.destinations.models import Destination
from app.modules.park_fees.models import ParkFee
from app.modules.park_fees.schemas import ParkFeeCreate, ParkFeeRead, ParkFeeUpdate
from app.modules.park_fees.service import ParkFeeService

router = APIRouter(tags=["park-fees"])

READ = "park_fee:read"
MANAGE = "park_fee:manage"


@router.get("/destinations/{destination_id}/park-fees", response_model=list[ParkFeeRead])
async def list_park_fees(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    rows = (
        await db.execute(
            select(ParkFee)
            .where(ParkFee.destination_id == destination_id)
            .order_by(ParkFee.effective_from.desc())
        )
    ).scalars().all()
    return rows


@router.post(
    "/destinations/{destination_id}/park-fees", response_model=ParkFeeRead, status_code=201
)
async def create_park_fee(
    destination_id: uuid.UUID,
    body: ParkFeeCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    if body.effective_to < body.effective_from:
        raise AppError("effective_to must be on or after effective_from.")
    if body.child_max_age < body.child_min_age:
        raise AppError("child_max_age must be >= child_min_age.")
    await CRUDService(db, Destination).get(destination_id)  # 404 if missing
    data = body.model_dump()
    data["currency"] = body.currency.upper()
    fee = ParkFee(destination_id=destination_id, **data)
    db.add(fee)
    await db.commit()
    await db.refresh(fee)
    return fee


@router.get("/park-fees/{fee_id}", response_model=ParkFeeRead)
async def get_park_fee(
    fee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, ParkFee).get(fee_id)


@router.patch("/park-fees/{fee_id}", response_model=ParkFeeRead)
async def update_park_fee(
    fee_id: uuid.UUID,
    body: ParkFeeUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    data = body.model_dump(exclude_unset=True)
    if "currency" in data and data["currency"]:
        data["currency"] = data["currency"].upper()
    return await CRUDService(db, ParkFee).update(fee_id, data)


@router.get(
    "/destinations/{destination_id}/resolve-park-fee", response_model=ParkFeeRead
)
async def resolve_park_fee(
    destination_id: uuid.UUID,
    fee_type: str,
    residence_category_id: uuid.UUID,
    on_date: date,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await ParkFeeService(db).select_fee(
        destination_id=destination_id,
        fee_type=fee_type,
        residence_category_id=residence_category_id,
        on_date=on_date,
    )
