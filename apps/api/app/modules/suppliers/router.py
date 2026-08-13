from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService, slugify
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.suppliers.models import Supplier
from app.modules.suppliers.schemas import SupplierCreate, SupplierRead, SupplierUpdate

router = APIRouter(prefix="/suppliers", tags=["reference"])


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("supplier:read")),
):
    return await CRUDService(db, Supplier).list()


@router.post("", response_model=SupplierRead, status_code=201)
async def create_supplier(
    body: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("supplier:manage")),
):
    data = body.model_dump()
    data["slug"] = slugify(data.get("slug") or data["name"])
    return await CRUDService(db, Supplier).create(data)


@router.get("/{supplier_id}", response_model=SupplierRead)
async def get_supplier(
    supplier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("supplier:read")),
):
    return await CRUDService(db, Supplier).get(supplier_id)


@router.patch("/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: uuid.UUID,
    body: SupplierUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("supplier:manage")),
):
    return await CRUDService(db, Supplier).update(
        supplier_id, body.model_dump(exclude_unset=True)
    )
