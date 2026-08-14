"""Park-fee selection and computation.

Pure functions (`classify_age`, `compute_park_fee`) are unit-testable without a
database and are reused by the pricing engine (Stage 2.8). The class method
`select_fee` does the deterministic DB lookup.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.park_fees.models import ParkFee


def classify_age(age: int, child_min_age: int, child_max_age: int) -> str:
    """Classify a traveller by age using this fee's own bounds.

    age < child_min_age            -> "infant"
    child_min_age..child_max_age   -> "child"
    age > child_max_age            -> "adult"
    """
    if age < child_min_age:
        return "infant"
    if age <= child_max_age:
        return "child"
    return "adult"


def compute_park_fee(
    *,
    adult_fee: Decimal,
    child_fee: Decimal,
    infant_fee: Decimal,
    adults: int,
    ages: list[int],
    days: int,
    child_min_age: int,
    child_max_age: int,
) -> dict:
    """Total fee for a group over `days`.

    `adults` counts travellers with no age captured (assumed adult); `ages`
    holds the ages of children/infants (and any age-known adults). Fees are
    charged per person per day.
    """
    counts = {"adult": adults, "child": 0, "infant": 0}
    for age in ages:
        counts[classify_age(age, child_min_age, child_max_age)] += 1

    adult_total = adult_fee * counts["adult"] * days
    child_total = child_fee * counts["child"] * days
    infant_total = infant_fee * counts["infant"] * days
    return {
        "counts": counts,
        "days": days,
        "adult_total": adult_total,
        "child_total": child_total,
        "infant_total": infant_total,
        "total": adult_total + child_total + infant_total,
    }


class ParkFeeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_fee(
        self,
        *,
        destination_id: uuid.UUID,
        fee_type: str,
        residence_category_id: uuid.UUID,
        on_date: date,
    ) -> ParkFee:
        stmt = (
            select(ParkFee)
            .where(
                ParkFee.destination_id == destination_id,
                ParkFee.fee_type == fee_type,
                ParkFee.residence_category_id == residence_category_id,
                ParkFee.is_active.is_(True),
                ParkFee.effective_from <= on_date,
                ParkFee.effective_to >= on_date,
            )
            .order_by(ParkFee.effective_from.desc())
            .limit(1)
        )
        fee = (await self.db.execute(stmt)).scalar_one_or_none()
        if fee is None:
            raise NotFoundError(
                "No park fee found for the given destination, fee type, "
                "residence category, and date."
            )
        return fee
