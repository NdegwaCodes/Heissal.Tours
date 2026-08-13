"""Accommodation rate selection — the deterministic pricing lookup."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.accommodations.models import AccommodationRate


class AccommodationRateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_rate(
        self,
        *,
        room_type_id: uuid.UUID,
        meal_plan_id: uuid.UUID,
        residence_category_id: uuid.UUID,
        stay_date: date,
    ) -> AccommodationRate:
        """Return the single active rate whose period contains `stay_date`.

        If several overlap, the one with the latest `effective_from` wins.
        A miss raises NotFoundError — a price is never assumed.
        """
        stmt = (
            select(AccommodationRate)
            .where(
                AccommodationRate.room_type_id == room_type_id,
                AccommodationRate.meal_plan_id == meal_plan_id,
                AccommodationRate.residence_category_id == residence_category_id,
                AccommodationRate.is_active.is_(True),
                AccommodationRate.effective_from <= stay_date,
                AccommodationRate.effective_to >= stay_date,
            )
            .order_by(AccommodationRate.effective_from.desc())
            .limit(1)
        )
        rate = (await self.db.execute(stmt)).scalar_one_or_none()
        if rate is None:
            raise NotFoundError(
                "No accommodation rate found for the given room type, meal plan, "
                "residence category, and date."
            )
        return rate
