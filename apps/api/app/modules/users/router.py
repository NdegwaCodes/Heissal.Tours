"""User admin endpoints (RBAC-guarded)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.session import get_db
from app.modules.users.models import User
from app.modules.users.schemas import (
    AssignRolesRequest,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
) -> list[UserRead]:
    service = UserService(db)
    return [UserRead.from_user(u) for u in await service.list_users()]


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("user:create")),
) -> UserRead:
    service = UserService(db, actor=actor, ip=await client_ip(request))
    user = await service.create_user(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role_keys=body.role_keys,
    )
    return UserRead.from_user(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("user:read")),
) -> UserRead:
    service = UserService(db)
    return UserRead.from_user(await service.get_user(user_id))


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("user:update")),
) -> UserRead:
    service = UserService(db, actor=actor, ip=await client_ip(request))
    user = await service.update_user(
        user_id, full_name=body.full_name, is_active=body.is_active
    )
    return UserRead.from_user(user)


@router.put("/{user_id}/roles", response_model=UserRead)
async def assign_roles(
    user_id: uuid.UUID,
    body: AssignRolesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("user:manage_roles")),
) -> UserRead:
    service = UserService(db, actor=actor, ip=await client_ip(request))
    user = await service.assign_roles(user_id, body.role_keys)
    return UserRead.from_user(user)
