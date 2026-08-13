"""RBAC service — read permissions/roles, create roles, set role permissions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.audit.service import AuditService
from app.modules.rbac.models import Permission, Role, role_permissions
from app.modules.users.models import User


class RBACService:
    def __init__(self, db: AsyncSession, actor: User | None = None, ip: str | None = None):
        self.db = db
        self.actor = actor
        self.ip = ip
        self.audit = AuditService(db)

    @property
    def _actor_id(self) -> uuid.UUID | None:
        return self.actor.id if self.actor else None

    async def list_permissions(self) -> Sequence[Permission]:
        return (
            await self.db.execute(select(Permission).order_by(Permission.key))
        ).scalars().all()

    async def list_roles(self) -> Sequence[Role]:
        return (await self.db.execute(select(Role).order_by(Role.key))).scalars().all()

    async def get_role(self, role_id: uuid.UUID) -> Role:
        role = (await self.db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    async def _permissions_by_keys(self, keys: list[str]) -> list[Permission]:
        if not keys:
            return []
        perms = list(
            (await self.db.execute(select(Permission).where(Permission.key.in_(keys))))
            .scalars()
            .all()
        )
        missing = set(keys) - {p.key for p in perms}
        if missing:
            raise NotFoundError(f"Unknown permission(s): {', '.join(sorted(missing))}")
        return perms

    async def create_role(
        self, *, key: str, name: str, description: str | None, permission_keys: list[str]
    ) -> Role:
        key = key.lower().strip()
        exists = (await self.db.execute(select(Role).where(Role.key == key))).scalar_one_or_none()
        if exists:
            raise ConflictError("A role with this key already exists.")
        perms = await self._permissions_by_keys(permission_keys)
        role = Role(key=key, name=name, description=description, is_system=False)
        self.db.add(role)
        await self.db.flush()
        for perm in perms:
            await self.db.execute(
                insert(role_permissions).values(role_id=role.id, permission_id=perm.id)
            )
        await self.audit.record(
            actor_user_id=self._actor_id,
            action="ROLE_CREATE",
            entity_type="role",
            entity_id=str(role.id),
            new_value={"key": key, "permissions": permission_keys},
            ip=self.ip,
        )
        await self.db.commit()
        return await self.get_role(role.id)

    async def set_role_permissions(
        self, role_id: uuid.UUID, permission_keys: list[str]
    ) -> Role:
        role = await self.get_role(role_id)
        old_keys = sorted(p.key for p in role.permissions)
        perms = await self._permissions_by_keys(permission_keys)
        await self.db.execute(
            delete(role_permissions).where(role_permissions.c.role_id == role.id)
        )
        for perm in perms:
            await self.db.execute(
                insert(role_permissions).values(role_id=role.id, permission_id=perm.id)
            )
        await self.audit.record(
            actor_user_id=self._actor_id,
            action="ROLE_PERMISSIONS_SET",
            entity_type="role",
            entity_id=str(role.id),
            old_value={"permissions": old_keys},
            new_value={"permissions": sorted(permission_keys)},
            ip=self.ip,
        )
        await self.db.commit()
        return await self.get_role(role.id)
