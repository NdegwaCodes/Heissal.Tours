"""Quote domain — quotes, immutable versions, travellers, legs and selections.

Assembly only (Stage 2.7): these tables capture *what was requested* — the
client, travellers (with per-child ages), the ordered legs and the accommodation
/ activity / transport selections, plus per-quote markup/discount/tax overrides.

Computed pricing lives in ``quote_versions`` (an immutable JSONB snapshot + the
headline totals) and ``quote_items`` (per-line cost + client price); those are
written by the PricingEngine in Stage 2.8 and are never silently overwritten —
re-pricing a sent quote appends a new version.

Money is ``Numeric``/``Decimal`` + an explicit currency; dates are plain calendar
dates (a stay night), timestamps are TIMESTAMPTZ via the mixins.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

# Quote lifecycle states. Editable data flows, not a hard-coded price rule.
QUOTE_STATUSES = ("draft", "sent", "accepted", "declined", "expired")


class QuoteCounter(Base):
    """Per-year sequence backing human-readable quote numbers (HTQ-YYYY-NNNN).

    A single row per year; the number service locks the row (``FOR UPDATE``) and
    increments ``last_value`` so concurrent quote creation cannot collide.
    """

    __tablename__ = "quote_counters"

    # A natural key (the calendar year), not an auto-incrementing surrogate.
    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Quote(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "quotes"

    quote_number: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True, nullable=False)
    presentation_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    residence_category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("residence_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Per-quote overrides; NULL means "use the business default" (pricing config).
    markup_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    tax_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)

    # Points at the latest immutable snapshot (set once pricing runs, Stage 2.8).
    # use_alter breaks the quotes<->quote_versions circular FK at DDL time.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "quote_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_quotes_current_version_id",
        ),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    travellers: Mapped[list[QuoteTraveller]] = relationship(
        "QuoteTraveller",
        back_populates="quote",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="QuoteTraveller.quote_id",
    )
    legs: Mapped[list[QuoteLeg]] = relationship(
        "QuoteLeg",
        back_populates="quote",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QuoteLeg.sequence",
        foreign_keys="QuoteLeg.quote_id",
    )
    transport: Mapped[list[QuoteTransport]] = relationship(
        "QuoteTransport",
        back_populates="quote",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="QuoteTransport.quote_id",
    )


class QuoteVersion(UUIDPKMixin, Base):
    """Immutable computed snapshot of a quote at a point in time (Stage 2.8)."""

    __tablename__ = "quote_versions"
    __table_args__ = (UniqueConstraint("quote_id", "version_number", name="uq_quote_version_no"),)

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Full computed breakdown (line items, subtotals) as returned by the engine.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    internal_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[list[QuoteItem]] = relationship(
        "QuoteItem",
        back_populates="version",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class QuoteTraveller(UUIDPKMixin, Base):
    __tablename__ = "quote_travellers"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    # adult | child | infant — classification may be recomputed from age by the
    # engine using each fee's own age bounds, but the requested type is recorded.
    traveller_type: Mapped[str] = mapped_column(String(10), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)

    quote: Mapped[Quote] = relationship(
        "Quote", back_populates="travellers", foreign_keys=[quote_id]
    )


class QuoteLeg(UUIDPKMixin, Base):
    __tablename__ = "quote_legs"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="RESTRICT"), nullable=False
    )
    nights: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    check_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_out: Mapped[date | None] = mapped_column(Date, nullable=True)

    quote: Mapped[Quote] = relationship("Quote", back_populates="legs", foreign_keys=[quote_id])
    accommodations: Mapped[list[QuoteAccommodation]] = relationship(
        "QuoteAccommodation",
        back_populates="leg",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    activities: Mapped[list[QuoteActivity]] = relationship(
        "QuoteActivity",
        back_populates="leg",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class QuoteAccommodation(UUIDPKMixin, Base):
    __tablename__ = "quote_accommodations"

    leg_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_legs.id", ondelete="CASCADE"), nullable=False
    )
    accommodation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="RESTRICT"), nullable=False
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="RESTRICT"), nullable=False
    )
    meal_plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="RESTRICT"), nullable=False
    )
    rooms: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    nights: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    leg: Mapped[QuoteLeg] = relationship("QuoteLeg", back_populates="accommodations")


class QuoteActivity(UUIDPKMixin, Base):
    __tablename__ = "quote_activities"

    leg_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_legs.id", ondelete="CASCADE"), nullable=False
    )
    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("activities.id", ondelete="RESTRICT"), nullable=False
    )
    day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adults: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    children: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    leg: Mapped[QuoteLeg] = relationship("QuoteLeg", back_populates="activities")


class QuoteTransport(UUIDPKMixin, Base):
    __tablename__ = "quote_transport"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False
    )
    estimated_km: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    quote: Mapped[Quote] = relationship(
        "Quote", back_populates="transport", foreign_keys=[quote_id]
    )


class QuoteItem(UUIDPKMixin, Base):
    """A computed line on a version: internal cost AND client price (Stage 2.8).

    Both are stored so an internal serializer can show cost/margin while the
    client serializer exposes only the price — a schema-level separation, not a
    UI toggle.
    """

    __tablename__ = "quote_items"

    version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_versions.id", ondelete="CASCADE"), nullable=False
    )
    # accommodation | park_fee | activity | transport | other
    category: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1, nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    internal_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    version: Mapped[QuoteVersion] = relationship("QuoteVersion", back_populates="items")
