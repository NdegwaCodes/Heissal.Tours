from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PricingConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_markup_pct: Decimal
    default_tax_pct: Decimal
    default_discount_pct: Decimal
    discount_approval_threshold_pct: Decimal
    max_discount_pct: Decimal
    quote_validity_days: int


class PricingConfigUpdate(BaseModel):
    """Partial update — only the provided fields are changed."""

    default_markup_pct: Decimal | None = Field(default=None, ge=0)
    default_tax_pct: Decimal | None = Field(default=None, ge=0)
    default_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    discount_approval_threshold_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    quote_validity_days: int | None = Field(default=None, ge=1)


class ConversionRead(BaseModel):
    """Result of an FX conversion via the ExchangeRate service."""

    amount: Decimal
    from_currency: str
    to_currency: str
    on_date: str
    rate: Decimal
    converted: Decimal
