"""Residence category — the configurable pricing tier (citizen / resident / …).

Drives which rate rows the pricing engine selects, and a default presentation
currency. Editable data, never hard-coded; categories may differ per park, so
rate rows reference a category rather than assuming a fixed set.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class ResidenceCategory(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "residence_categories"

    key: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Default presentation currency suggested when this category is chosen.
    default_currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
