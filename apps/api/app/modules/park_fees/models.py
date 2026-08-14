"""Park & conservation fees.

Fees are per destination (park/conservancy), fee type, and residence category,
effective-dated, with configurable child-age bounds — because parks define
"child" differently. Never hard-coded; a missing fee is an explicit error.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class ParkFee(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "park_fees"
    __table_args__ = (
        UniqueConstraint(
            "destination_id",
            "fee_type",
            "residence_category_id",
            "effective_from",
            name="uq_park_fee_period",
        ),
    )

    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    # park_entry | conservancy | camping | other
    fee_type: Mapped[str] = mapped_column(String(30), default="park_entry", nullable=False)
    residence_category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("residence_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    adult: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    child: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    infant: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    # Configurable per-fee child-age bounds (parks differ).
    child_min_age: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    child_max_age: Mapped[int] = mapped_column(Integer, default=11, nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
