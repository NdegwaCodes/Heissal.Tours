"""PricingConfig — business-wide markup / discount / tax / validity defaults.

These are *configurable data*, not hard-coded business rules: they live in the
``app_settings`` table under the ``pricing`` key and are edited by admins/finance
via the API (master prompt §44 — "nothing hard-coded"). The values below are only
the initial defaults used the first time the config is read, before an admin has
saved any. Each field carries the percentages the pricing engine (Stage 2.8)
applies; a specific quote may still override them within approval limits.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# The app_settings row key under which the pricing config JSON is stored.
PRICING_SETTINGS_KEY = "pricing"


class PricingConfig(BaseModel):
    """Typed view over the ``pricing`` app-setting (all percentages are 0..100)."""

    model_config = ConfigDict(extra="ignore")

    default_markup_pct: Decimal = Field(default=Decimal("20"), ge=0)
    default_tax_pct: Decimal = Field(default=Decimal("0"), ge=0)
    default_discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    # A quote discount at or above this percentage requires manager approval.
    discount_approval_threshold_pct: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    # Discounts above this hard ceiling are rejected outright.
    max_discount_pct: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    # How long an issued quote stays valid, in days.
    quote_validity_days: int = Field(default=14, ge=1)

    # --- Stage 3 quotation build-up (design doc section 3.6) --------------- #
    # Contingency sits inside the cost basis, so it accrues profit.
    contingency_pct: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    # Profit is a fixed 24% on the whole sum, confirmed by the client. It lives
    # here rather than in code so the exception case is an edit, not a deploy.
    profit_pct: Decimal = Field(default=Decimal("24"), ge=0, le=100)
    # The per-person figure is rounded UP to this step before being multiplied
    # back out to the group total, so the document's two headline numbers agree.
    per_person_rounding: Decimal = Field(default=Decimal("100"), gt=0)
    # A Stage 3 quotation is valid for 30 days, unlike the Stage 2 default.
    quotation_validity_days: int = Field(default=30, ge=1)
