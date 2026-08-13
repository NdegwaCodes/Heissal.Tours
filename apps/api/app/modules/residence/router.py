from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.residence.models import ResidenceCategory
from app.modules.residence.schemas import (
    ResidenceCategoryCreate,
    ResidenceCategoryRead,
    ResidenceCategoryUpdate,
)

router = APIRouter(prefix="/residence-categories", tags=["reference"])


def _svc(db: AsyncSession) -> CRUDService[ResidenceCategory]:
    return CRUDService(db, ResidenceCategory)


@router.get("", response_model=list[ResidenceCategoryRead])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("residence:read")),
):
    return await _svc(db).list()


@router.post("", response_model=ResidenceCategoryRead, status_code=201)
async def create_category(
    body: ResidenceCategoryCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("residence:manage")),
):
    data = body.model_dump()
    data["key"] = slugify(data["key"])
    return await _svc(db).create(data)


@router.get("/{category_id}", response_model=ResidenceCategoryRead)
async def get_category(
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("residence:read")),
):
    return await _svc(db).get(category_id)


@router.patch("/{category_id}", response_model=ResidenceCategoryRead)
async def update_category(
    category_id: uuid.UUID,
    body: ResidenceCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("residence:manage")),
):
    return await _svc(db).update(category_id, body.model_dump(exclude_unset=True))
