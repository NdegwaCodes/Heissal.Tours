"""Pricing configuration service + the pure selling-price math.

The pure functions here (``apply_markup``, ``apply_discount``, ``apply_tax``,
``compute_price_breakdown``) implement steps 6–10 of the PricingEngine algorithm
(design doc §4). They take an already-summed internal cost (the engine converts
every line to the presentation currency via the ExchangeRate service first) and
turn it into a selling price, discount, tax, profit and margin. They are pure,
Decimal-only and unit-tested so Stage 2.8 can compose them with confidence.

``PricingConfigService`` reads/writes the business-wide defaults stored in the
``app_settings`` table under the ``pricing`` key.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.pricing.config import PRICING_SETTINGS_KEY, PricingConfig
from app.modules.settings.models import AppSetting

_HUNDRED = Decimal("100")


# --------------------------------------------------------------------------- #
# Pure pricing math (no DB, no I/O) — reused and unit-tested by Stage 2.8.
# --------------------------------------------------------------------------- #

def _pct(value: Decimal) -> Decimal:
    """Turn a 0..100 percentage into a 0..1 fraction."""
    return value / _HUNDRED


def apply_markup(internal_cost: Decimal, markup_pct: Decimal) -> Decimal:
    """selling_subtotal = internal_cost × (1 + markup%)."""
    if internal_cost < 0:
        raise ValueError("internal_cost must be >= 0")
    if markup_pct < 0:
        raise ValueError("markup_pct must be >= 0")
    return internal_cost * (Decimal(1) + _pct(markup_pct))


def apply_discount(
    subtotal: Decimal,
    *,
    discount_pct: Decimal | None = None,
    discount_amount: Decimal | None = None,
) -> Decimal:
    """Return the discount value (percentage of ``subtotal`` or a fixed amount).

    At most one of ``discount_pct`` / ``discount_amount`` may be given. The value
    is clamped to ``[0, subtotal]`` so a discount can never make a line negative.
    """
    if discount_pct is not None and discount_amount is not None:
        raise ValueError("Provide either discount_pct or discount_amount, not both.")
    if discount_pct is not None:
        if discount_pct < 0:
            raise ValueError("discount_pct must be >= 0")
        value = subtotal * _pct(discount_pct)
    elif discount_amount is not None:
        if discount_amount < 0:
            raise ValueError("discount_amount must be >= 0")
        value = discount_amount
    else:
        value = Decimal(0)
    if value < 0:
        value = Decimal(0)
    if value > subtotal:
        value = subtotal
    return value


def apply_tax(after_discount: Decimal, tax_pct: Decimal) -> Decimal:
    """tax = after_discount × tax%."""
    if tax_pct < 0:
        raise ValueError("tax_pct must be >= 0")
    return after_discount * _pct(tax_pct)


def compute_price_breakdown(
    internal_cost: Decimal,
    *,
    markup_pct: Decimal,
    discount_pct: Decimal | None = None,
    discount_amount: Decimal | None = None,
    tax_pct: Decimal = Decimal("0"),
    discount_approval_threshold_pct: Decimal | None = None,
) -> dict[str, Any]:
    """Full selling-price breakdown for an already-summed internal cost.

    Mirrors design §4 steps 6–10. All amounts are returned as raw ``Decimal``
    (no rounding); ``gross_margin`` is a fraction in ``[0, 1]``.
    """
    selling_subtotal = apply_markup(internal_cost, markup_pct)
    discount_value = apply_discount(
        selling_subtotal, discount_pct=discount_pct, discount_amount=discount_amount
    )
    after_discount = selling_subtotal - discount_value
    tax = apply_tax(after_discount, tax_pct)
    selling_price = after_discount + tax
    gross_profit = selling_price - internal_cost
    gross_margin = (gross_profit / selling_price) if selling_price > 0 else Decimal(0)

    effective_discount_pct = (
        (discount_value / selling_subtotal * _HUNDRED)
        if selling_subtotal > 0
        else Decimal(0)
    )
    needs_approval = (
        discount_approval_threshold_pct is not None
        and effective_discount_pct >= discount_approval_threshold_pct
    )

    return {
        "internal_cost": internal_cost,
        "markup_pct": markup_pct,
        "selling_subtotal": selling_subtotal,
        "discount_value": discount_value,
        "after_discount": after_discount,
        "tax_pct": tax_pct,
        "tax": tax,
        "selling_price": selling_price,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "needs_approval": needs_approval,
    }


# --------------------------------------------------------------------------- #
# Config persistence (app_settings["pricing"]).
# --------------------------------------------------------------------------- #

class PricingConfigService:
    """Reads and updates the business-wide pricing defaults."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _row(self) -> AppSetting | None:
        stmt = select(AppSetting).where(AppSetting.key == PRICING_SETTINGS_KEY)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get(self) -> PricingConfig:
        """Return the stored config, or the defaults if none is saved yet."""
        row = await self._row()
        if row is None:
            return PricingConfig()
        return PricingConfig.model_validate(row.value)

    async def update(
        self, patch: dict[str, Any], *, updated_by: Any | None = None
    ) -> PricingConfig:
        """Merge ``patch`` onto the current config and persist it (upsert)."""
        current = await self.get()
        merged = PricingConfig.model_validate({**current.model_dump(), **patch})
        payload = merged.model_dump(mode="json")

        row = await self._row()
        if row is None:
            row = AppSetting(key=PRICING_SETTINGS_KEY, value=payload, updated_by=updated_by)
            self.db.add(row)
        else:
            row.value = payload
            row.updated_by = updated_by
        try:
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            raise ConflictError("Could not save pricing configuration.") from exc
        return merged
