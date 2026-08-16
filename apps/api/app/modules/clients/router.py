from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.clients.models import Client
from app.modules.clients.schemas import ClientCreate, ClientRead, ClientUpdate

router = APIRouter(tags=["clients"])


@router.get("/clients", response_model=list[ClientRead])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("client:read")),
):
    return await CRUDService(db, Client).list()


@router.post("/clients", response_model=ClientRead, status_code=201)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("client:manage")),
):
    data = body.model_dump()
    if data.get("email") is not None:
        data["email"] = str(data["email"]).lower()
    return await CRUDService(db, Client).create(data)


@router.get("/clients/{client_id}", response_model=ClientRead)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("client:read")),
):
    return await CRUDService(db, Client).get(client_id)


@router.patch("/clients/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("client:manage")),
):
    data = body.model_dump(exclude_unset=True)
    if data.get("email") is not None:
        data["email"] = str(data["email"]).lower()
    return await CRUDService(db, Client).update(client_id, data)
