"""Generic async CRUD helper for simple catalogue/reference models.

Keeps the many Stage 2 reference modules DRY. Custom domain logic (rate
selection, FX lookup, the pricing engine) lives in dedicated services, not here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.base_class import Base

ModelT = TypeVar("ModelT", bound=Base)


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "item"


class CRUDService(Generic[ModelT]):
    def __init__(self, db: AsyncSession, model: type[ModelT], pk: str = "id"):
        self.db = db
        self.model = model
        self.pk = pk

    def _pk_col(self):
        return getattr(self.model, self.pk)

    async def list(self, *, limit: int = 200, active_only: bool = False) -> Sequence[ModelT]:
        stmt = select(self.model)
        if active_only and hasattr(self.model, "is_active"):
            stmt = stmt.where(self.model.is_active.is_(True))  # type: ignore[attr-defined]
        stmt = stmt.limit(limit)
        return (await self.db.execute(stmt)).scalars().all()

    async def get(self, ident: Any) -> ModelT:
        obj = (
            await self.db.execute(select(self.model).where(self._pk_col() == ident))
        ).scalar_one_or_none()
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} not found.")
        return obj

    async def create(self, data: dict[str, Any]) -> ModelT:
        obj = self.model(**data)
        self.db.add(obj)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError(f"{self.model.__name__} violates a uniqueness constraint.") from exc
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def update(self, ident: Any, data: dict[str, Any]) -> ModelT:
        obj = await self.get(ident)
        for key, value in data.items():
            setattr(obj, key, value)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError(f"{self.model.__name__} violates a uniqueness constraint.") from exc
        await self.db.commit()
        await self.db.refresh(obj)
        return obj
