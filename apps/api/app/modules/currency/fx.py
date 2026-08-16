"""AdminExchangeRateProvider — the DB-backed FX provider (Stage 2.6).

Reads the admin-maintained ``exchange_rates`` table and resolves conversions
deterministically:

* identity when the currencies are equal;
* a **direct** rate (base=from, quote=to), latest ``effective_from <= on_date``;
* otherwise an **inverse** rate (base=to, quote=from), reciprocated;
* otherwise an explicit error — a missing rate is never assumed to be 1:1.

Only direct and inverse pairs are resolved. Cross-currency triangulation
through a pivot is intentionally *not* inferred: silently multiplying two
independently-set rates would fabricate a price the admin never approved.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.integrations.exchange_rate import apply_rate
from app.modules.currency.models import ExchangeRate


class AdminExchangeRateProvider:
    """FX conversion backed by the admin-set ``exchange_rates`` rows."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _latest_rate(
        self, base: str, quote: str, on_date: date
    ) -> Decimal | None:
        stmt = (
            select(ExchangeRate.rate)
            .where(
                ExchangeRate.base_currency == base,
                ExchangeRate.quote_currency == quote,
                ExchangeRate.effective_from <= on_date,
            )
            .order_by(ExchangeRate.effective_from.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def effective_rate(self, from_ccy: str, to_ccy: str, on_date: date) -> Decimal:
        """Return the rate (target per unit of source) as of ``on_date``.

        Identity is 1. Prefers a direct rate; falls back to the reciprocal of an
        inverse rate. Raises :class:`NotFoundError` if neither exists.
        """
        from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
        if from_ccy == to_ccy:
            return Decimal(1)

        direct = await self._latest_rate(from_ccy, to_ccy, on_date)
        if direct is not None:
            return direct

        inverse = await self._latest_rate(to_ccy, from_ccy, on_date)
        if inverse is not None and inverse != 0:
            return Decimal(1) / inverse

        raise NotFoundError(
            f"No exchange rate for {from_ccy}->{to_ccy} on or before {on_date}."
        )

    async def convert(
        self,
        amount: Decimal,
        from_ccy: str,
        to_ccy: str,
        on_date: date,
    ) -> Decimal:
        rate = await self.effective_rate(from_ccy, to_ccy, on_date)
        return apply_rate(amount, rate)
