"""Fleet: vehicles and effective-dated fuel prices.

Vehicle CRUD boilerplate was scaffolded (scripts/scaffold_module.py); FuelPrice
and the transport-cost logic (service.py) are hand-written. Fuel prices are
effective-dated per fuel type — the service reads the latest price on/ before a
date. Nothing is hard-coded.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class Vehicle(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "vehicles"

    slug: Mapped[str] = mapped_column(String(280), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(
        String(60), default="safari_land_cruiser", nullable=False
    )
    registration: Mapped[str | None] = mapped_column(String(30), nullable=True)
    passenger_capacity: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(20), default="diesel", nullable=False)
    fuel_consumption_kmpl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    cost_per_km: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_operating_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    driver_cost_per_day: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FuelPrice(UUIDPKMixin, Base):
    __tablename__ = "fuel_prices"
    __table_args__ = (
        UniqueConstraint("fuel_type", "effective_from", name="uq_fuel_price_period"),
    )

    fuel_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    price_per_litre: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
