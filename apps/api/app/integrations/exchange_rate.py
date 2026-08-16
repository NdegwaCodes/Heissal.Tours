"""ExchangeRateProvider seam — the currency-conversion interface the pricing
engine depends on, plus the pure math the concrete providers share.

The engine never assumes 1:1 across different currencies and never reaches into
a specific FX source directly; it depends only on this ``ExchangeRateProvider``
protocol. Stage 2.6 ships the first concrete implementation
(:class:`app.modules.currency.fx.AdminExchangeRateProvider`, backed by the
admin-set ``exchange_rates`` table). A live-API provider can replace it later
without touching the engine.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol


def apply_rate(amount: Decimal, rate: Decimal) -> Decimal:
    """Convert ``amount`` using ``rate`` (units of target per unit of source).

    Pure and precision-preserving — no rounding here; presentation rounding to a
    currency's decimal places is the caller's concern.
    """
    return amount * rate


class ExchangeRateProvider(Protocol):
    """Converts money between currencies as of a given date.

    Implementations MUST:
    - return ``amount`` unchanged when ``from_ccy == to_ccy`` (identity), and
    - raise (never silently assume 1:1) when no rate path exists for the date.
    """

    async def convert(
        self,
        amount: Decimal,
        from_ccy: str,
        to_ccy: str,
        on_date: date,
    ) -> Decimal: ...
