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
    # This is the fallback; the map below is what actually applies.
    per_person_rounding: Decimal = Field(default=Decimal("100"), gt=0)
    # **A step per currency.** One global step cannot be right for both: KES 100
    # is a rounding on a five-figure shilling price and a 48% mark-up on a
    # two-figure dollar one. Rounding USD 135 per person up to USD 200 does not
    # look like a rounding convention to a client, it looks like a different
    # quote — and it is the kind of error that loses a booking without anyone
    # learning why. USD 1 confirmed by the client 2026-09-04; the other foreign
    # currencies default to 1 for the same reason rather than waiting to be
    # discovered. Anything not listed falls back to ``per_person_rounding``.
    per_person_rounding_by_currency: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "USD": Decimal("1"),
            "EUR": Decimal("1"),
            "GBP": Decimal("1"),
        }
    )
    # A Stage 3 quotation is valid for 30 days, unlike the Stage 2 default.
    quotation_validity_days: int = Field(default=30, ge=1)

    # --- Stage 3.4 quote shape (design doc §1, §3.7) ----------------------- #
    # "3-9 hotels plus 1-2 BnB options." Held as configurable bounds rather than
    # literals in a validator, and split by whether pricing had to add a chef —
    # the only distinction in the data that actually means "the guests feed
    # themselves". A property category string would not: it is free text.
    min_catered_options: int = Field(default=3, ge=1)
    max_catered_options: int = Field(default=9, ge=1)
    min_self_catering_options: int = Field(default=1, ge=0)
    max_self_catering_options: int = Field(default=2, ge=0)

    def rounding_for(self, currency: str) -> Decimal:
        """The per-person rounding step for one currency.

        A method rather than a lookup at each call site: pricing rounds in three
        places (the option build-up, each cohort's own figure, and an optional
        add-on), and a fallback spelled out three times is a fallback that will
        eventually differ in one of them.
        """
        step = self.per_person_rounding_by_currency.get(currency.upper())
        if step is None or step <= 0:
            return self.per_person_rounding
        return step
