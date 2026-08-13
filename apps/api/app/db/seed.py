"""Idempotent seed: permissions, system roles, and the first superuser.

Safe to run repeatedly — it upserts by natural key and never duplicates.
Run with: `python -m app.db.seed` (or `make seed`).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import insert, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.modules.rbac.models import Permission, Role, role_permissions
from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS
from app.modules.users.models import User


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        # --- Permissions ---
        existing_perms = {
            p.key: p for p in (await db.execute(select(Permission))).scalars().all()
        }
        for key, desc in PERMISSIONS.items():
            perm = existing_perms.get(key)
            if perm is None:
                perm = Permission(key=key, description=desc)
                db.add(perm)
                existing_perms[key] = perm
            else:
                perm.description = desc
        await db.flush()

        # --- Roles + their permissions ---
        existing_roles = {
            r.key: r for r in (await db.execute(select(Role))).scalars().all()
        }
        for key, definition in ROLE_DEFINITIONS.items():
            role = existing_roles.get(key)
            if role is None:
                role = Role(
                    key=key,
                    name=definition["name"],
                    description=definition["description"],
                    is_system=True,
                )
                db.add(role)
                await db.flush()
                existing_roles[key] = role

            # Current links (queried explicitly — never via the lazy relationship,
            # which cannot load in an async context).
            current_perm_ids = set(
                (
                    await db.execute(
                        select(role_permissions.c.permission_id).where(
                            role_permissions.c.role_id == role.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            desired_keys = set(definition["permissions"])
            for pkey in desired_keys:
                perm_id = existing_perms[pkey].id
                if perm_id not in current_perm_ids:
                    await db.execute(
                        insert(role_permissions).values(
                            role_id=role.id, permission_id=perm_id
                        )
                    )
        await db.flush()

        # --- First superuser ---
        email = settings.FIRST_SUPERUSER_EMAIL.lower().strip()
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=email,
                full_name=settings.FIRST_SUPERUSER_NAME,
                hashed_password=hash_password(settings.FIRST_SUPERUSER_PASSWORD),
                is_active=True,
                is_superuser=True,
            )
            db.add(user)
            print(f"[seed] created superuser {email}")
        else:
            print(f"[seed] superuser {email} already exists")

        await db.commit()
        print("[seed] done")


if __name__ == "__main__":
    asyncio.run(seed())
