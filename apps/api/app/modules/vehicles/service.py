"""Fuel-price selection and transport-cost computation.

`compute_transport_cost` is a pure function (unit-tested, reused by the pricing
engine): fuel cost from distance / consumption × fuel price, plus driver and
operating costs over the trip's days. Fuel consumption on game drives differs
from highway driving, so a `consumption_multiplier` seam is provided (default 1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.vehicles.models import FuelPrice


def compute_transport_cost(
    *,
    distance_km: Decimal,
    consumption_kmpl: Decimal,
    fuel_price_per_litre: Decimal,
    days: int,
    driver_cost_per_day: Decimal,
    daily_operating_cost: Decimal,
    consumption_multiplier: Decimal = Decimal("1"),
) -> dict:
    if consumption_kmpl <= 0:
        raise ValueError("consumption_kmpl must be > 0")
    effective_kmpl = consumption_kmpl / consumption_multiplier
    fuel_litres = distance_km / effective_kmpl
    fuel_cost = fuel_litres * fuel_price_per_litre
    driver_total = driver_cost_per_day * days
    operating_total = daily_operating_cost * days
    total = fuel_cost + driver_total + operating_total
    return {
        "fuel_litres": fuel_litres,
        "fuel_cost": fuel_cost,
        "driver_total": driver_total,
        "operating_total": operating_total,
        "total": total,
    }


class FuelPriceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_price(self, *, fuel_type: str, on_date: date) -> FuelPrice:
        stmt = (
            select(FuelPrice)
            .where(
                FuelPrice.fuel_type == fuel_type,
                FuelPrice.effective_from <= on_date,
            )
            .order_by(FuelPrice.effective_from.desc())
            .limit(1)
        )
        price = (await self.db.execute(stmt)).scalar_one_or_none()
        if price is None:
            raise NotFoundError(
                f"No fuel price found for fuel type '{fuel_type}' on or before {on_date}."
            )
        return price
