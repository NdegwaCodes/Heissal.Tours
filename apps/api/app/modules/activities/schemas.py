from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ActivityBase(BaseModel):
    name: str
    destination_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    description: str | None = None
    duration_minutes: int | None = None
    is_optional: bool = True
    is_active: bool = True
    # A mandatory activity is costed into the package and named under the
    # document's Included list; an optional one is priced beside it. The column
    # has existed since §3.1 and nothing could set it, which made every
    # activity optional in practice — the same gap the transport segments had.
    is_mandatory: bool = False
    has_own_section: bool = False


class ActivityCreate(ActivityBase):
    slug: str | None = None


class ActivityUpdate(BaseModel):
    name: str | None = None
    destination_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    description: str | None = None
    duration_minutes: int | None = None
    is_optional: bool | None = None
    is_active: bool | None = None
    is_mandatory: bool | None = None
    has_own_section: bool | None = None


class ActivityRead(ActivityBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    created_at: datetime


class ActivityRateCreate(BaseModel):
    residence_category_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3)
    adult_price: Decimal = Field(ge=0)
    child_price: Decimal = Field(ge=0)
    effective_from: date
    effective_to: date
    is_active: bool = True


class ActivityRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    activity_id: uuid.UUID
    residence_category_id: uuid.UUID
    currency: str
    adult_price: Decimal
    child_price: Decimal
    effective_from: date
    effective_to: date
    is_active: bool
