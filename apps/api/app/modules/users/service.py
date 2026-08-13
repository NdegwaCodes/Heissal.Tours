"""User service — CRUD + role assignment, with audit logging."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.security import hash_password
from app.modules.audit.service import AuditService
from app.modules.rbac.models import Role, user_roles
from app.modules.users.models import User


class UserService:
    def __init__(self, db: AsyncSession, actor: User | None = None, ip: str | None = None):
        self.db = db
        self.actor = actor
        self.ip = ip
        self.audit = AuditService(db)

    @property
    def _actor_id(self) -> uuid.UUID | None:
        return self.actor.id if self.actor else None

    async def list_users(self, limit: int = 50) -> Sequence[User]:
        result = await self.db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
        return result.scalars().all()

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def _roles_by_keys(self, keys: list[str]) -> list[Role]:
        if not keys:
            return []
        result = await self.db.execute(select(Role).where(Role.key.in_(keys)))
        roles = list(result.scalars().all())
        found = {r.key for r in roles}
        missing = set(keys) - found
        if missing:
            raise NotFoundError(f"Unknown role(s): {', '.join(sorted(missing))}")
        return roles

    async def create_user(
        self, *, email: str, password: str, full_name: str | None, role_keys: list[str]
    ) -> User:
        email = email.lower().strip()
        exists = (
            await self.db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if exists:
            raise ConflictError("A user with this email already exists.")

        roles = await self._roles_by_keys(role_keys)
        user = User(email=email, full_name=full_name, hashed_password=hash_password(password))
        self.db.add(user)
        await self.db.flush()

        for role in roles:
            await self.db.execute(
                insert(user_roles).values(
                    user_id=user.id, role_id=role.id, assigned_by=self._actor_id
                )
            )

        await self.audit.record(
            actor_user_id=self._actor_id,
            action="USER_CREATE",
            entity_type="user",
            entity_id=str(user.id),
            new_value={"email": email, "roles": role_keys},
            ip=self.ip,
        )
        await self.db.commit()
        return await self.get_user(user.id)

    async def update_user(
        self, user_id: uuid.UUID, *, full_name: str | None, is_active: bool | None
    ) -> User:
        user = await self.get_user(user_id)
        old = {"full_name": user.full_name, "is_active": user.is_active}
        if full_name is not None:
            user.full_name = full_name
        if is_active is not None:
            user.is_active = is_active
        await self.audit.record(
            actor_user_id=self._actor_id,
            action="USER_UPDATE",
            entity_type="user",
            entity_id=str(user.id),
            old_value=old,
            new_value={"full_name": user.full_name, "is_active": user.is_active},
            ip=self.ip,
        )
        await self.db.commit()
        return await self.get_user(user.id)

    async def assign_roles(self, user_id: uuid.UUID, role_keys: list[str]) -> User:
        user = await self.get_user(user_id)
        old_keys = sorted(r.key for r in user.roles)
        roles = await self._roles_by_keys(role_keys)

        await self.db.execute(delete(user_roles).where(user_roles.c.user_id == user.id))
        for role in roles:
            await self.db.execute(
                insert(user_roles).values(
                    user_id=user.id, role_id=role.id, assigned_by=self._actor_id
                )
            )
        await self.audit.record(
            actor_user_id=self._actor_id,
            action="USER_ROLES_SET",
            entity_type="user",
            entity_id=str(user.id),
            old_value={"roles": old_keys},
            new_value={"roles": sorted(role_keys)},
            ip=self.ip,
        )
        await self.db.commit()
        return await self.get_user(user.id)
