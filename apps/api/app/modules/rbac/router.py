"""Role & permission admin endpoints (RBAC-guarded)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_permission
from app.db.session import get_db
from app.modules.rbac.schemas import (
    PermissionRead,
    RoleCreate,
    RoleRead,
    SetRolePermissionsRequest,
)
from app.modules.rbac.service import RBACService
from app.modules.users.models import User

router = APIRouter(tags=["rbac"])


@router.get("/permissions", response_model=list[PermissionRead])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("role:read")),
) -> list[PermissionRead]:
    service = RBACService(db)
    return [PermissionRead.model_validate(p) for p in await service.list_permissions()]


@router.get("/roles", response_model=list[RoleRead])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("role:read")),
) -> list[RoleRead]:
    service = RBACService(db)
    return [RoleRead.model_validate(r) for r in await service.list_roles()]


@router.post("/roles", response_model=RoleRead, status_code=201)
async def create_role(
    body: RoleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("role:create")),
) -> RoleRead:
    service = RBACService(db, actor=actor, ip=await client_ip(request))
    role = await service.create_role(
        key=body.key,
        name=body.name,
        description=body.description,
        permission_keys=body.permission_keys,
    )
    return RoleRead.model_validate(role)


@router.put("/roles/{role_id}/permissions", response_model=RoleRead)
async def set_role_permissions(
    role_id: uuid.UUID,
    body: SetRolePermissionsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("role:update")),
) -> RoleRead:
    service = RBACService(db, actor=actor, ip=await client_ip(request))
    role = await service.set_role_permissions(role_id, body.permission_keys)
    return RoleRead.model_validate(role)
