from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CurrencyBase(BaseModel):
    name: str
    symbol: str | None = None
    decimal_places: int = 2
    is_active: bool = True


class CurrencyCreate(CurrencyBase):
    code: str = Field(min_length=3, max_length=3)


class CurrencyUpdate(BaseModel):
    name: str | None = None
    symbol: str | None = None
    decimal_places: int | None = None
    is_active: bool | None = None


class CurrencyRead(CurrencyBase):
    model_config = ConfigDict(from_attributes=True)
    code: str


class ExchangeRateCreate(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)
    quote_currency: str = Field(min_length=3, max_length=3)
    rate: Decimal = Field(gt=0)
    effective_from: date
    source: str = "manual"


class ExchangeRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_from: date
    source: str
    created_at: datetime
