from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ParkFeeCreate(BaseModel):
    fee_type: str = "park_entry"
    residence_category_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3)
    adult: Decimal = Field(ge=0)
    child: Decimal = Field(ge=0)
    infant: Decimal = Field(default=Decimal("0"), ge=0)
    child_min_age: int = 3
    child_max_age: int = 11
    effective_from: date
    effective_to: date
    is_active: bool = True


class ParkFeeUpdate(BaseModel):
    fee_type: str | None = None
    currency: str | None = None
    adult: Decimal | None = None
    child: Decimal | None = None
    infant: Decimal | None = None
    child_min_age: int | None = None
    child_max_age: int | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None


class ParkFeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    destination_id: uuid.UUID
    fee_type: str
    residence_category_id: uuid.UUID
    currency: str
    adult: Decimal
    child: Decimal
    infant: Decimal
    child_min_age: int
    child_max_age: int
    effective_from: date
    effective_to: date
    is_active: bool
