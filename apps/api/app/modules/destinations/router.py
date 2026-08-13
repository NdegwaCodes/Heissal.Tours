from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.destinations.models import Destination
from app.modules.destinations.schemas import (
    DestinationCreate,
    DestinationRead,
    DestinationUpdate,
)

router = APIRouter(prefix="/destinations", tags=["reference"])


@router.get("", response_model=list[DestinationRead])
async def list_destinations(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("destination:read")),
):
    return await CRUDService(db, Destination).list()


@router.post("", response_model=DestinationRead, status_code=201)
async def create_destination(
    body: DestinationCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("destination:manage")),
):
    data = body.model_dump()
    data["slug"] = slugify(data.get("slug") or data["name"])
    return await CRUDService(db, Destination).create(data)


@router.get("/{destination_id}", response_model=DestinationRead)
async def get_destination(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("destination:read")),
):
    return await CRUDService(db, Destination).get(destination_id)


@router.patch("/{destination_id}", response_model=DestinationRead)
async def update_destination(
    destination_id: uuid.UUID,
    body: DestinationUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("destination:manage")),
):
    return await CRUDService(db, Destination).update(
        destination_id, body.model_dump(exclude_unset=True)
    )
