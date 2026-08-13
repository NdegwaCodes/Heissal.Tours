"""Destinations — cities, parks, conservancies, hubs, beaches.

Structured (not free text). Parks/conservancies carry park fees (Stage 2.3);
accommodations and activities reference a destination.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class Destination(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "destinations"

    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    # city | park | conservancy | hub | beach | other
    type: Mapped[str] = mapped_column(String(30), default="other", index=True, nullable=False)
    country: Mapped[str] = mapped_column(String(100), default="Kenya", nullable=False)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
