"""Accommodation catalogue: properties, room types, meal plans, and seasonal rates.

Rate selection is deterministic: given (accommodation, room_type, meal_plan,
residence_category, stay_date) exactly one active rate whose date range contains
the stay date is chosen. No rate is ever assumed — a miss is an explicit error.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class MealPlan(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "meal_plans"

    code: Mapped[str] = mapped_column(String(10), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Accommodation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "accommodations"

    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(280), unique=True, index=True, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    # lodge | tented_camp | camp | hotel | resort | guesthouse | other
    category: Mapped[str] = mapped_column(String(40), default="lodge", nullable=False)
    star_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    check_in_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "14:00"
    check_out_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "10:00"
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    images: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    room_types: Mapped[list[RoomType]] = relationship(
        "RoomType",
        back_populates="accommodation",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class RoomType(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "room_types"

    accommodation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # single/double/twin/…
    code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    max_occupancy: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    accommodation: Mapped[Accommodation] = relationship(
        "Accommodation", back_populates="room_types"
    )


class AccommodationRate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "accommodation_rates"
    __table_args__ = (
        UniqueConstraint(
            "room_type_id",
            "meal_plan_id",
            "residence_category_id",
            "effective_from",
            name="uq_accommodation_rate_period",
        ),
    )

    accommodation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    meal_plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="RESTRICT"), nullable=False
    )
    residence_category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("residence_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    season_name: Mapped[str] = mapped_column(String(60), default="Standard", nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_per_night: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    child_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    single_supplement: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    min_nights: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
