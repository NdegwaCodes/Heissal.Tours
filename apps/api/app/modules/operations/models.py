"""The people who run a trip, and what is committed to it (§8.1).

§7.1 turned an accepted quote into a booking. Nothing then said *who was
driving*. §2.5 had put vehicles in the database, but only as a costing input —
a Land Cruiser with a fuel consumption and a daily rate — so two bookings could
be priced with the same vehicle over the same week and nothing anywhere
noticed.

Two tables close that.

**``crew``** is the register of drivers and guides. One table rather than two,
because in this market a **driver-guide** is usually one person: modelling them
as a driver row plus a guide row would mean assigning the same human twice,
double-booking them against themselves, and counting them twice on a cost
sheet. So ``roles`` is a list on the row.

**``trip_assignments``** is what is committed to a booking and when. Not a
``trips`` table: a booking already carries the dates, the headcount and the
reference, and a second row saying the same things is a second thing to keep in
step. An assignment is one vehicle *or* one person, over a window, and a
booking has as many as it needs — a group of twelve in two Land Cruisers with
two driver-guides is four rows and no special case.

The window is stored rather than derived from the booking, because a vehicle
leaving Nairobi the night before a coast pickup is out that night, and a fleet
calendar that says otherwise will hand it to somebody else on the Sunday.
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
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class CrewMember(UUIDPKMixin, TimestampMixin, Base):
    """A driver, a guide, or — usually — both."""

    __tablename__ = "crew"
    __table_args__ = (
        Index("ix_crew_active", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: ``driver``, ``guide``, ``driver_guide`` — a list, because one person is
    #: often more than one of them and the alternative is a duplicate human.
    #: A JSONB list rather than a join table: it is read on every assignment
    #: check and never queried the other way round.
    roles: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )

    #: The driving licence, and the day it runs out. Stored as a date rather
    #: than a valid/invalid flag for one reason: a licence expiring in the
    #: middle of a safari passes every check made on the Monday, and the group
    #: is in Tsavo when it lapses. Only a date can catch that.
    licence_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    licence_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: A guiding licence is a separate document from a driving one — KPSGA and
    #: the county PSV badge are not the same thing, and a person can hold one
    #: without the other.
    guide_licence_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    guide_licence_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: What they speak. The reason a particular person goes on a particular
    #: trip more often than any other: a German-speaking guide is the whole
    #: booking for some clients.
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    #: Set where the person is a freelancer or comes through an agency, so a
    #: trip run on somebody else's driver is visible as such.
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TripAssignment(UUIDPKMixin, TimestampMixin, Base):
    """One vehicle or one person, committed to one booking, over a window."""

    __tablename__ = "trip_assignments"
    __table_args__ = (
        # Exactly one of the two. A row with neither is a commitment to
        # nothing; a row with both would need two clash checks and would make
        # "which vehicle is out on the 5th" a query with a branch in it.
        CheckConstraint(
            "(vehicle_id is null) <> (crew_id is null)",
            name="ck_assignment_one_subject",
        ),
        CheckConstraint("ends_on >= starts_on", name="ck_assignment_window"),
        CheckConstraint(
            "role in ('driver', 'guide', 'driver_guide', 'vehicle')",
            name="ck_assignment_role",
        ),
        # The two queries that matter: what is on this booking, and what is
        # this vehicle or person doing over these dates.
        Index("ix_assignment_booking", "booking_id"),
        Index("ix_assignment_vehicle_dates", "vehicle_id", "starts_on", "ends_on"),
        Index("ix_assignment_crew_dates", "crew_id", "starts_on", "ends_on"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="RESTRICT"),
        nullable=True,
    )
    crew_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("crew.id", ondelete="RESTRICT"), nullable=True
    )

    #: ``vehicle`` for a vehicle; the crew role otherwise. On the row rather
    #: than inferred from the person, because somebody who is down as a
    #: driver-guide can be sent out on one trip to drive and on another purely
    #: to guide, and the trip sheet has to say which.
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Both inclusive, and wider than the client's dates where an operator says
    #: so — see the module docstring.
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)

    #: Why this one, in the operator's words: "client asked for Joseph again",
    #: "only 4x4 free that week". The thing that stops the next person undoing
    #: a deliberate choice.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Set when an operator overrode a clash on purpose, with the reason. The
    #: rules refuse a double-booking; an operator who knows the first trip is
    #: about to be cancelled needs a way through, and a way that leaves a
    #: record of who decided.
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


#: The ``role`` a vehicle assignment carries. Not a crew role, and kept here
#: rather than in the pure rules because it is a storage detail: the rules deal
#: in vehicles and people, not in a discriminator column.
VEHICLE = "vehicle"


class TripLog(UUIDPKMixin, TimestampMixin, Base):
    """What a vehicle actually did on one trip (§8.2).

    Every quote charges a drive from a distance and a ``fuel_consumption_kmpl``
    somebody typed onto a ``vehicles`` row once (§2.5). Nothing has ever
    recorded what the vehicle **did**, so the figure at the centre of every
    transport line has never been checked against a receipt. This is the row
    that makes it checkable.

    One log per vehicle assignment, not per booking: a group in two Land
    Cruisers is two odometers and two sets of receipts, and pooling them would
    lose exactly the comparison worth making.
    """

    __tablename__ = "trip_logs"
    __table_args__ = (
        # One log per assignment. A second would double the distance in every
        # fleet average, which is the kind of error that looks like a fact.
        UniqueConstraint("assignment_id", name="uq_trip_log_assignment"),
        CheckConstraint(
            "odometer_in_km is null or odometer_in_km >= odometer_out_km",
            name="ck_trip_log_odometer",
        ),
        CheckConstraint("odometer_out_km >= 0", name="ck_trip_log_out_positive"),
        # The fleet history query: this vehicle, most recent first.
        Index("ix_trip_log_vehicle", "vehicle_id", "started_on"),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("trip_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Denormalised from the assignment so the fleet history is one query and
    #: survives the assignment being deleted... which it does not, because the
    #: cascade takes the log with it. Kept anyway: every question asked of this
    #: table starts "for this vehicle", and a join to find that out on every
    #: one of them is a join for nothing.
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Out and in. ``in`` is nullable because a log is opened when the vehicle
    #: leaves and closed when it comes back, and the days in between are
    #: exactly when somebody wants to know where it is.
    odometer_out_km: Mapped[Decimal] = mapped_column(Numeric(12, 1), nullable=False)
    odometer_in_km: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 1), nullable=True
    )
    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Who was at the wheel. Nullable and separate from the crew assignment on
    #: purpose: a driver swap mid-trip happens, and the log should say who
    #: actually drove rather than who was rostered.
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("crew.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    fills: Mapped[list[FuelFill]] = relationship(
        "FuelFill",
        back_populates="log",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="FuelFill.bought_on",
    )

    @property
    def distance_km(self) -> Decimal | None:
        if self.odometer_in_km is None:
            return None
        return self.odometer_in_km - self.odometer_out_km

    @property
    def is_open(self) -> bool:
        return self.odometer_in_km is None


class FuelFill(UUIDPKMixin, TimestampMixin, Base):
    """One fuel receipt against one trip log (§8.2).

    **Litres and money, both from the receipt.** Neither is derived from the
    other: a litre price computed by division looks like the same information
    and is not — it loses the partial fill, the price that changed halfway
    through the trip, and the pump out at Voi that was charging a premium,
    which is exactly what somebody is looking for when they ask why a trip cost
    what it did.
    """

    __tablename__ = "fuel_fills"
    __table_args__ = (
        CheckConstraint("litres > 0", name="ck_fuel_fill_litres"),
        CheckConstraint("amount > 0", name="ck_fuel_fill_amount"),
        Index("ix_fuel_fill_log", "trip_log_id"),
    )

    trip_log_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trip_logs.id", ondelete="CASCADE"),
        nullable=False,
    )
    litres: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    #: Money is an amount **and** a currency here as everywhere else. A
    #: cross-border run that fuelled in Tanzania is two lines on the report,
    #: not one wrong one.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    bought_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: Where, and the receipt number. What makes a line checkable against a
    #: shoebox of paper at the end of the month.
    station: Mapped[str | None] = mapped_column(String(160), nullable=True)
    receipt_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    log: Mapped[TripLog] = relationship("TripLog", back_populates="fills")
