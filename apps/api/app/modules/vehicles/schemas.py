from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class VehicleBase(BaseModel):
    name: str
    vehicle_type: str = "safari_land_cruiser"
    registration: str | None = None
    passenger_capacity: int = 6
    fuel_type: str = "diesel"
    fuel_consumption_kmpl: Decimal = Field(gt=0)
    cost_per_km: Decimal | None = None
    daily_operating_cost: Decimal = Decimal("0")
    driver_cost_per_day: Decimal = Decimal("0")
    currency: str = Field(min_length=3, max_length=3)
    supplier_id: uuid.UUID | None = None
    is_active: bool = True


class VehicleCreate(VehicleBase):
    slug: str | None = None


class VehicleUpdate(BaseModel):
    name: str | None = None
    vehicle_type: str | None = None
    registration: str | None = None
    passenger_capacity: int | None = None
    fuel_type: str | None = None
    fuel_consumption_kmpl: Decimal | None = None
    cost_per_km: Decimal | None = None
    daily_operating_cost: Decimal | None = None
    driver_cost_per_day: Decimal | None = None
    currency: str | None = None
    supplier_id: uuid.UUID | None = None
    is_active: bool | None = None


class VehicleRead(VehicleBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    created_at: datetime


# --- Fuel prices ---
class FuelPriceCreate(BaseModel):
    fuel_type: str
    price_per_litre: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    effective_from: date
    source: str = "manual"


class FuelPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    fuel_type: str
    price_per_litre: Decimal
    currency: str
    effective_from: date
    source: str
    created_at: datetime
