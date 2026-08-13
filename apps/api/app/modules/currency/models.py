"""Currencies and admin-set exchange rates.

Currencies are keyed by their ISO-4217 code (natural key). Exchange rates are
effective-dated; the ExchangeRate service reads the latest rate with
effective_from <= the quote date. No conversion is ever assumed 1:1 across
different currencies.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class Currency(TimestampMixin, Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)  # ISO-4217
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(8), nullable=True)
    decimal_places: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ExchangeRate(UUIDPKMixin, Base):
    __tablename__ = "exchange_rates"

    base_currency: Mapped[str] = mapped_column(String(3), index=True, nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), index=True, nullable=False)
    # 1 unit of base_currency = `rate` units of quote_currency.
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
