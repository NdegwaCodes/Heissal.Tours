"""Destination-scoped transport tariffs and transfer rates (Stage 3.1).

Two facts drive this module (design doc §3.8):

1. **Access mode belongs to the destination.** Some places are reached by rail,
   some only by road, so the offerable modes and their fares hang off the
   destination rather than being a property of the vehicle fleet.
2. **A transfer's price depends on destination AND vehicle type.** A Coaster and
   a 5–7 seater are different prices for the same leg, and the same vehicle costs
   differently in different places — so transfers are a lookup, not something
   derived from km and fuel like the safari vehicle model.

**Air travel is deliberately absent.** Heissal holds no ticketing licence, so
flights are never sold and ``air`` is not an offerable mode. Airport transfers
remain quotable here as ordinary road legs.

Both tables are effective-dated and VAT-aware for the same reasons the
accommodation and fuel rates are: fares move, and a stored rate must say whether
its number already contains VAT.
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
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: Line-haul modes we can actually sell. No "air" — see the module docstring.
TRANSPORT_MODES = ("road", "rail")

#: How a tariff multiplies out.
COST_BASES = ("per_person", "per_vehicle", "per_leg")


class DestinationTransportMode(UUIDPKMixin, TimestampMixin, Base):
    """A way of reaching a destination, and what it costs.

    Seeded values for SGR (per person, one way): economy KES 1,500, business
    KES 12,000. Held as rows rather than constants because fares change — the
    same reason fuel prices are a table.
    """

    __tablename__ = "destination_transport_modes"
    __table_args__ = (
        UniqueConstraint(
            "destination_id",
            "mode",
            "travel_class",
            "effective_from",
            name="uq_destination_transport_mode_period",
        ),
    )

    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    #: economy / business for rail. Empty string (not NULL) where a mode has no
    #: classes, so the uniqueness constraint above actually bites — in Postgres
    #: two NULLs are distinct and would allow duplicate periods.
    travel_class: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    #: Human label for the document, e.g. "SGR — Nairobi to Mombasa".
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cost_basis: Mapped[str] = mapped_column(String(20), default="per_person", nullable=False)
    #: One-way price on the stated basis; a return journey is two segments.
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    vat_inclusive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vat_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("16"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TransferRate(UUIDPKMixin, TimestampMixin, Base):
    """Price of one transfer leg, keyed on destination and vehicle type."""

    __tablename__ = "transfer_rates"
    __table_args__ = (
        UniqueConstraint(
            "destination_id",
            "vehicle_type",
            "route_label",
            "effective_from",
            name="uq_transfer_rate_period",
        ),
    )

    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Matches the vocabulary on ``vehicles.vehicle_type`` (coaster, safari_van,
    #: saloon …) so a quote's chosen vehicle maps straight onto a transfer price.
    vehicle_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    passenger_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: e.g. "Nairobi CBD → SGR terminal". Empty string rather than NULL so the
    #: uniqueness constraint holds (see the note on travel_class above).
    route_label: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    price_per_leg: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    vat_inclusive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vat_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("16"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
