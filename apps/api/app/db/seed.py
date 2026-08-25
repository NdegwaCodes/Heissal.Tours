"""Idempotent seed: permissions, system roles, and the first superuser.

Safe to run repeatedly — it upserts by natural key and never duplicates.
Run with: `python -m app.db.seed` (or `make seed`).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import MealPlan
from app.modules.currency.models import Currency, ExchangeRate
from app.modules.rbac.models import Permission, Role, role_permissions
from app.modules.rbac.permissions import PERMISSIONS, ROLE_DEFINITIONS
from app.modules.residence.models import ResidenceCategory
from app.modules.users.models import User

# Editable starting data (admins can change/add these later — not business rules).
DEFAULT_CURRENCIES = [
    ("KES", "Kenyan Shilling", "KSh", 2),
    ("USD", "US Dollar", "$", 2),
    ("EUR", "Euro", "€", 2),
    ("GBP", "Pound Sterling", "£", 2),
]

# The five tiers Kenyan pricing actually uses, matched to the columns on the KWS
# Conservation Fees 2025 schedule — which prices East African Citizen and Kenya
# Resident in shillings and Non-Resident and African Citizen in dollars.
#
# The currency here is a **billing default**, not a rule: it suggests what to
# quote this category in. What a supplier *charges us* travels on each rate row,
# and the two differ in practice — KWS bills a Kenya Resident in KES while
# Swahili Beach's STO sheet quotes resident rates in USD. Cohort pricing converts
# between them, so a category's default currency never constrains a cost line.
#
# `african_citizen` is a national of an African country outside East Africa, and
# is charged materially less than a non-resident (Amboseli: USD 50 against 90).
# It had no category until the KWS schedule was read, so every such traveller was
# being quoted as a full non-resident.
DEFAULT_RESIDENCE_CATEGORIES = [
    ("citizen", "Kenyan Citizen", 1, "KES"),
    ("ea_resident", "East African Citizen", 2, "KES"),
    ("resident", "Kenya Resident", 3, "KES"),
    ("african_citizen", "African Citizen", 4, "USD"),
    ("non_resident", "Non-Resident", 5, "USD"),
]

# The USD->KES rate is a CONTRACT rate, not a market rate: supplier rate sheets
# state it themselves (Swahili Beach's 2026 STO agreement: "FOR RESIDENT RATES
# IN USD YOU MUST PLEASE USE CONVERSION RATE OF 130 KES"). It is seeded as an
# ordinary effective-dated exchange_rates row rather than a constant in code, so
# it stays auditable and an admin can supersede it by adding a later row when a
# supplier restates it — no deploy needed. Without this row the engine raises
# NotFoundError on any USD property quoted in KES, which until now only tests
# ever set up.
CONTRACT_FX_RATES = [
    ("USD", "KES", Decimal("130"), date(2026, 1, 1)),
]

DEFAULT_MEAL_PLANS = [
    ("RO", "Room Only"),
    ("BB", "Bed & Breakfast"),
    ("HB", "Half Board"),
    ("FB", "Full Board"),
    ("AI", "All Inclusive"),
]


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

        # --- Reference defaults (Stage 2) — editable, not business rules ---
        existing_ccy = {
            c.code for c in (await db.execute(select(Currency))).scalars().all()
        }
        for code, name, symbol, dp in DEFAULT_CURRENCIES:
            if code not in existing_ccy:
                db.add(Currency(code=code, name=name, symbol=symbol, decimal_places=dp))

        existing_rc = {
            r.key for r in (await db.execute(select(ResidenceCategory))).scalars().all()
        }
        for key, name, order, ccy in DEFAULT_RESIDENCE_CATEGORIES:
            if key not in existing_rc:
                db.add(
                    ResidenceCategory(
                        key=key, name=name, sort_order=order, default_currency_code=ccy
                    )
                )

        for base, quote, rate, eff in CONTRACT_FX_RATES:
            exists = (
                await db.execute(
                    select(ExchangeRate.id)
                    .where(
                        ExchangeRate.base_currency == base,
                        ExchangeRate.quote_currency == quote,
                        ExchangeRate.effective_from == eff,
                    )
                    .limit(1)
                )
            ).scalar()
            if exists is None:
                db.add(
                    ExchangeRate(
                        base_currency=base,
                        quote_currency=quote,
                        rate=rate,
                        effective_from=eff,
                        source="contract",
                    )
                )

        existing_mp = {
            m.code for m in (await db.execute(select(MealPlan))).scalars().all()
        }
        for code, name in DEFAULT_MEAL_PLANS:
            if code not in existing_mp:
                db.add(MealPlan(code=code, name=name))

        await db.commit()
        print("[seed] done")


if __name__ == "__main__":
    asyncio.run(seed())
