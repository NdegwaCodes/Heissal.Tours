"""Schemas for the road network and the transport tariffs (§4.2)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RouteBase(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    #: Driven kilometres, one way. Not derived from coordinates — see the model.
    distance_km: Decimal = Field(gt=0)
    drive_time_minutes: int = Field(gt=0)
    #: ``Vehicle.vehicle_type`` values this road takes. Empty means any.
    required_vehicle_types: list[str] = Field(default_factory=list)
    notes: str | None = None
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> RouteBase:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class RouteCreate(RouteBase):
    origin_id: uuid.UUID
    destination_id: uuid.UUID

    @model_validator(mode="after")
    def _distinct(self) -> RouteCreate:
        if self.origin_id == self.destination_id:
            raise ValueError("a route needs two different places")
        return self


class RouteUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    distance_km: Decimal | None = Field(default=None, gt=0)
    drive_time_minutes: int | None = Field(default=None, gt=0)
    required_vehicle_types: list[str] | None = None
    notes: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None


class RouteRead(RouteBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    origin_id: uuid.UUID
    destination_id: uuid.UUID
    created_at: datetime


class TransferRateCreate(BaseModel):
    """A transfer tariff, keyed on destination and vehicle type (§3.10).

    Reachable through the API at last. Readiness has been telling operators to
    "load the fare before issuing" since 3.10 while the only way to do it was a
    seeder — a blocking message whose fix needed a developer.
    """

    vehicle_type: str = Field(max_length=40)
    passenger_capacity: int | None = Field(default=None, gt=0)
    route_label: str = Field(default="", max_length=200)
    price_per_leg: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    vat_inclusive: bool = True
    vat_pct: Decimal = Field(default=Decimal("16"), ge=0, le=100)
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True
    notes: str | None = None


class TransferRateRead(TransferRateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    destination_id: uuid.UUID
    created_at: datetime


class TransportModeCreate(BaseModel):
    """A way of reaching a destination and its one-way fare (§3.10)."""

    mode: str = Field(max_length=10)
    travel_class: str = Field(default="", max_length=20)
    label: str | None = Field(default=None, max_length=200)
    cost_basis: str = Field(default="per_person", max_length=20)
    price: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    vat_inclusive: bool = True
    vat_pct: Decimal = Field(default=Decimal("16"), ge=0, le=100)
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True
    notes: str | None = None


class TransportModeRead(TransportModeCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    destination_id: uuid.UUID
    created_at: datetime
