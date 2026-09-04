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
    CheckConstraint,
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


class Route(UUIDPKMixin, TimestampMixin, Base):
    """A road route between two destinations, as the operator knows it (§4.2).

    **Hand-entered, deliberately.** The catalogue holds latitude and longitude,
    and the temptation is to derive distance from them — but a great-circle
    line is not a Kenyan road: Nairobi to the Mara is about 225 km straight and
    about 270 km driven, and the drive time depends far more on the surface
    than on either figure. A routing API would answer the distance and still
    not know that the last 40 km wants a 4×4 in April. The client's operations
    team drives these roads; this is where what they know is recorded.

    ``required_vehicle_types`` is the point of the table as much as the
    distance. A route the client states needs a Land Cruiser, quoted on a
    saloon, is both a mis-price (the vehicle costs more) and a trip that does
    not happen — so it blocks at readiness rather than being noticed on the
    road.

    Directional, and read either way round. Distance is symmetric and time
    roughly is, so a lookup falls back to the reverse row and says it did
    (§4.2); where the return genuinely differs — a one-way road, a ferry
    queue that only bites southbound — the operator enters the second row and
    it wins for that direction.

    Effective-dated like every other reference row here, because the seasonal
    fact is exactly the one worth dating: the same route is a saloon drive in
    January and a 4×4 drive in April.
    """

    __tablename__ = "routes"
    __table_args__ = (
        UniqueConstraint(
            "origin_id",
            "destination_id",
            "effective_from",
            name="uq_route_period",
        ),
        CheckConstraint("distance_km > 0", name="ck_route_distance_positive"),
        CheckConstraint(
            "drive_time_minutes > 0", name="ck_route_drive_time_positive"
        ),
        CheckConstraint("origin_id <> destination_id", name="ck_route_endpoints_differ"),
    )

    origin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: What a client reads on the itinerary — "Diani to the Maasai Mara".
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Road kilometres, one way. Numeric rather than integer because a short
    #: transfer leg is measured in tenths and fuel is charged on it.
    distance_km: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    #: Driving time in minutes, as the operator times it rather than as an
    #: average speed implies. Minutes so "4h 45" needs no rounding.
    drive_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The vehicle types this road actually takes, as ``Vehicle.vehicle_type``
    #: values. Empty means any: most tarmac routes do not care. Stated by the
    #: client per route, which is why it is a list of their own vocabulary
    #: rather than a derived "is it 4×4" flag.
    required_vehicle_types: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    #: Free text for the fact that does not fit a column — "impassable after
    #: heavy rain", "ferry queue adds an hour on Fridays". Printed on the
    #: internal worksheet, never on the client's page.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
