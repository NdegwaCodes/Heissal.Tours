"""Activity rate selection + pure cost computation."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.activities.models import ActivityRate


def compute_activity_cost(
    *, adult_price: Decimal, child_price: Decimal, adults: int, children: int
) -> dict:
    """Cost of an activity for a group (one occurrence)."""
    adult_total = adult_price * adults
    child_total = child_price * children
    return {
        "adults": adults,
        "children": children,
        "adult_total": adult_total,
        "child_total": child_total,
        "total": adult_total + child_total,
    }


class ActivityRateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_rate(
        self,
        *,
        activity_id: uuid.UUID,
        residence_category_id: uuid.UUID,
        on_date: date,
    ) -> ActivityRate:
        stmt = (
            select(ActivityRate)
            .where(
                ActivityRate.activity_id == activity_id,
                ActivityRate.residence_category_id == residence_category_id,
                ActivityRate.is_active.is_(True),
                ActivityRate.effective_from <= on_date,
                ActivityRate.effective_to >= on_date,
            )
            .order_by(ActivityRate.effective_from.desc())
            .limit(1)
        )
        rate = (await self.db.execute(stmt)).scalar_one_or_none()
        if rate is None:
            raise NotFoundError(
                "No activity rate found for the given activity, residence category, and date."
            )
        return rate
