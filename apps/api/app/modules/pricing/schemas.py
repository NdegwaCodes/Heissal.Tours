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

    # --- Stage 3 quotation build-up and quote shape ------------------------ #
    # Exposed because the design doc requires them to be an admin edit rather
    # than a deploy: "profit is a fixed 24% ... it lives in the existing
    # app_settings pricing config (not hard-coded), with a per-quote override for
    # the exception case" (§3.6). Left out of the schema they were configurable in
    # name only.
    contingency_pct: Decimal
    profit_pct: Decimal
    per_person_rounding: Decimal
    per_person_rounding_by_currency: dict[str, Decimal]
    quotation_validity_days: int
    min_catered_options: int
    max_catered_options: int
    min_self_catering_options: int
    max_self_catering_options: int


class PricingConfigUpdate(BaseModel):
    """Partial update — only the provided fields are changed."""

    default_markup_pct: Decimal | None = Field(default=None, ge=0)
    default_tax_pct: Decimal | None = Field(default=None, ge=0)
    default_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    discount_approval_threshold_pct: Decimal | None = Field(default=None, ge=0, le=100)
    max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    quote_validity_days: int | None = Field(default=None, ge=1)
    contingency_pct: Decimal | None = Field(default=None, ge=0, le=100)
    profit_pct: Decimal | None = Field(default=None, ge=0, le=100)
    per_person_rounding: Decimal | None = Field(default=None, gt=0)
    # Replaces the whole map when sent — a per-currency step is a policy, and
    # merging keys would leave a removed currency silently in force.
    per_person_rounding_by_currency: dict[str, Decimal] | None = None
    quotation_validity_days: int | None = Field(default=None, ge=1)
    min_catered_options: int | None = Field(default=None, ge=1)
    max_catered_options: int | None = Field(default=None, ge=1)
    min_self_catering_options: int | None = Field(default=None, ge=0)
    max_self_catering_options: int | None = Field(default=None, ge=0)


class ConversionRead(BaseModel):
    """Result of an FX conversion via the ExchangeRate service."""

    amount: Decimal
    from_currency: str
    to_currency: str
    on_date: str
    rate: Decimal
    converted: Decimal
