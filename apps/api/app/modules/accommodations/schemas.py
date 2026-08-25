from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.vat import DEFAULT_VAT_PCT


# --- Meal plans ---
class MealPlanCreate(BaseModel):
    code: str = Field(max_length=10)
    name: str
    is_active: bool = True


class MealPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


# --- Room types ---
class RoomTypeCreate(BaseModel):
    name: str
    code: str | None = None
    max_occupancy: int = 2
    is_active: bool = True


class RoomTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accommodation_id: uuid.UUID
    name: str
    code: str | None
    max_occupancy: int
    is_active: bool


# --- Accommodations ---
class AccommodationBase(BaseModel):
    name: str
    destination_id: uuid.UUID
    supplier_id: uuid.UUID | None = None
    category: str = "lodge"
    star_rating: int | None = None
    description: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    website: str | None = None
    images: list[str] = []
    is_active: bool = True


class AccommodationCreate(AccommodationBase):
    slug: str | None = None


class AccommodationUpdate(BaseModel):
    name: str | None = None
    supplier_id: uuid.UUID | None = None
    category: str | None = None
    star_rating: int | None = None
    description: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    website: str | None = None
    images: list[str] | None = None
    is_active: bool | None = None


class AccommodationRead(AccommodationBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    created_at: datetime
    room_types: list[RoomTypeRead] = []


# --- Rates ---
class AccommodationRateCreate(BaseModel):
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    residence_category_id: uuid.UUID
    season_name: str = "Standard"
    effective_from: date
    effective_to: date
    currency: str = Field(min_length=3, max_length=3)
    rate_per_night: Decimal = Field(ge=0)
    child_rate: Decimal | None = Field(default=None, ge=0)
    single_supplement: Decimal | None = Field(default=None, ge=0)
    min_nights: int | None = None
    is_active: bool = True
    # Occupancy is part of a rate's identity (§3.3) — the same room is a
    # different price for one guest and for two — and it is in the uniqueness
    # key. Without it here a typed-in property could only ever hold one rate per
    # room/plan/residence/season, which is not how any real sheet is shaped.
    occupancy: int = Field(default=2, ge=1, le=20)
    # Stage 3 provenance. Defaults match the corpus (an inclusive rack rate with
    # no stated concession), so an existing caller keeps working unchanged.
    rate_kind: Literal["rack", "sto"] = "rack"
    supplier_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    # What the *source* quoted. An exclusive figure is grossed up on the way in
    # and stored inclusive (§3.2), so this is an input, not the stored state.
    vat_inclusive: bool = True
    vat_pct: Decimal = Field(default=DEFAULT_VAT_PCT, ge=0, le=100)
    child_min_age: int | None = Field(default=None, ge=0, le=17)
    child_max_age: int | None = Field(default=None, ge=0, le=17)


class AccommodationRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    residence_category_id: uuid.UUID
    season_name: str
    effective_from: date
    effective_to: date
    currency: str
    rate_per_night: Decimal
    child_rate: Decimal | None
    single_supplement: Decimal | None
    min_nights: int | None
    is_active: bool
    occupancy: int
    rate_kind: str
    supplier_discount_pct: Decimal | None
    vat_inclusive: bool
    vat_pct: Decimal
    child_min_age: int | None
    child_max_age: int | None
    source_document_id: uuid.UUID | None
