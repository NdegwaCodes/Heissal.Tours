from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


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
