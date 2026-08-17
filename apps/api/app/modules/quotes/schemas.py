from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.quotes.models import QUOTE_STATUSES

TRAVELLER_TYPES = ("adult", "child", "infant")


# --------------------------------------------------------------------------- #
# Input (assembly) schemas
# --------------------------------------------------------------------------- #

class TravellerIn(BaseModel):
    traveller_type: str
    age: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def _check_type(self) -> TravellerIn:
        if self.traveller_type not in TRAVELLER_TYPES:
            raise ValueError(f"traveller_type must be one of {TRAVELLER_TYPES}")
        return self


class AccommodationSelectionIn(BaseModel):
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    rooms: int = Field(default=1, ge=1)
    nights: int = Field(default=1, ge=1)


class ActivitySelectionIn(BaseModel):
    activity_id: uuid.UUID
    day: int | None = Field(default=None, ge=1)
    adults: int = Field(default=0, ge=0)
    children: int = Field(default=0, ge=0)


class LegIn(BaseModel):
    destination_id: uuid.UUID
    nights: int = Field(default=1, ge=1)
    check_in: date | None = None
    check_out: date | None = None
    accommodations: list[AccommodationSelectionIn] = Field(default_factory=list)
    activities: list[ActivitySelectionIn] = Field(default_factory=list)


class TransportIn(BaseModel):
    vehicle_id: uuid.UUID
    estimated_km: Decimal = Field(default=Decimal("0"), ge=0)
    days: int = Field(default=1, ge=1)


class QuoteCreate(BaseModel):
    client_id: uuid.UUID
    # Optional — default from the client's residence category / its currency.
    presentation_currency: str | None = Field(default=None, min_length=3, max_length=3)
    residence_category_id: uuid.UUID | None = None
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_pct: Decimal | None = Field(default=None, ge=0)
    travellers: list[TravellerIn] = Field(default_factory=list)
    legs: list[LegIn] = Field(default_factory=list)
    transport: list[TransportIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dates(self) -> QuoteCreate:
        if self.departure_date <= self.arrival_date:
            raise ValueError("departure_date must be after arrival_date")
        return self


class QuoteStatusUpdate(BaseModel):
    status: str

    @model_validator(mode="after")
    def _check_status(self) -> QuoteStatusUpdate:
        if self.status not in QUOTE_STATUSES:
            raise ValueError(f"status must be one of {QUOTE_STATUSES}")
        return self


# --------------------------------------------------------------------------- #
# Output schemas
# --------------------------------------------------------------------------- #

class TravellerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    traveller_type: str
    age: int | None


class AccommodationSelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    accommodation_id: uuid.UUID
    room_type_id: uuid.UUID
    meal_plan_id: uuid.UUID
    rooms: int
    nights: int


class ActivitySelectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    activity_id: uuid.UUID
    day: int | None
    adults: int
    children: int


class LegRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    destination_id: uuid.UUID
    nights: int
    check_in: date | None
    check_out: date | None
    accommodations: list[AccommodationSelectionRead]
    activities: list[ActivitySelectionRead]


class TransportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vehicle_id: uuid.UUID
    estimated_km: Decimal
    days: int


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    quote_number: str
    client_id: uuid.UUID
    status: str
    presentation_currency: str
    residence_category_id: uuid.UUID
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None
    discount_pct: Decimal | None
    tax_pct: Decimal | None
    current_version_id: uuid.UUID | None
    created_at: datetime
    travellers: list[TravellerRead]
    legs: list[LegRead]
    transport: list[TransportRead]


class QuoteSummary(BaseModel):
    """Lightweight list row (no nested assembly)."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    quote_number: str
    client_id: uuid.UUID
    status: str
    presentation_currency: str
    arrival_date: date
    departure_date: date
    created_at: datetime


# --------------------------------------------------------------------------- #
# Pricing (Stage 2.8)
# --------------------------------------------------------------------------- #

class CalculateRequest(BaseModel):
    """A transient quote to price without persisting (live quote builder)."""

    residence_category_id: uuid.UUID
    presentation_currency: str = Field(min_length=3, max_length=3)
    arrival_date: date
    departure_date: date
    markup_pct: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    tax_pct: Decimal | None = Field(default=None, ge=0)
    travellers: list[TravellerIn] = Field(default_factory=list)
    legs: list[LegIn] = Field(default_factory=list)
    transport: list[TransportIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dates(self) -> CalculateRequest:
        if self.departure_date <= self.arrival_date:
            raise ValueError("departure_date must be after arrival_date")
        return self


class PricingLineClient(BaseModel):
    """A costed line as the client sees it — price only, never cost."""

    category: str
    description: str
    quantity: Decimal
    client_price: Decimal


class PricingLineInternal(PricingLineClient):
    """Staff view — adds the internal cost and its source currency."""

    source_currency: str
    internal_cost: Decimal


class PricingResultClient(BaseModel):
    presentation_currency: str
    lines: list[PricingLineClient]
    selling_price: Decimal


class PricingResultInternal(BaseModel):
    presentation_currency: str
    lines: list[PricingLineInternal]
    markup_pct: Decimal
    discount_pct: Decimal
    tax_pct: Decimal
    internal_cost: Decimal
    selling_subtotal: Decimal
    discount_value: Decimal
    after_discount: Decimal
    tax: Decimal
    selling_price: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    needs_approval: bool


class QuoteItemClient(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    category: str
    description: str
    quantity: Decimal
    unit_price: Decimal


class QuoteItemInternal(QuoteItemClient):
    source_currency: str
    internal_cost: Decimal


class QuoteVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    selling_price: Decimal
    created_at: datetime


class QuoteVersionClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    selling_price: Decimal
    created_at: datetime
    items: list[QuoteItemClient]


class QuoteVersionInternalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int
    currency: str
    internal_cost: Decimal
    selling_price: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    created_at: datetime
    items: list[QuoteItemInternal]
