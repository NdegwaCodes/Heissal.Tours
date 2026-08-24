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
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

# Quote lifecycle states. Editable data flows, not a hard-coded price rule.
QUOTE_STATUSES = ("draft", "sent", "accepted", "declined", "expired")

# Who put a rejected candidate on the quote. The engine rewrites its own
# refusals every time the options are re-priced; an agent's typed one must
# survive that, so the two are told apart by column rather than by guessing
# from a NULL accommodation_id (a manual refusal may well name a property we
# hold in the catalogue).
REJECTION_SOURCES = ("engine", "manual")


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

    # -- Stage 3: group quoting ---------------------------------------------- #
    # Headcount for a group quote. A 25-person corporate booking is not entered
    # as 25 traveller rows, so this is the authority when set; NULL falls back to
    # counting `travellers`.
    pax_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stage 3 overrides on the margin build-up. NULL uses the business defaults
    # (profit 24%, contingency 5%) held in pricing config, never hard-coded.
    profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    contingency_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    # What the client asked for (full board / half board). Options may fall back
    # from it, which is recorded per option rather than by editing this.
    requested_meal_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="RESTRICT"), nullable=True
    )
    # Printed on the document; 30 days from issue by default. Past it an option
    # is re-priced rather than honoured, since supplier rates move.
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Which option the client actually chose — the highest-value field for CRM
    # analysis (does the Recommended flag match real behaviour?). Circular FK
    # against quote_options, so use_alter breaks the DDL cycle exactly as
    # current_version_id does above.
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "quote_options.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_quotes_selected_option_id",
        ),
        nullable=True,
    )
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
    options: Mapped[list[QuoteOption]] = relationship(
        "QuoteOption",
        back_populates="quote",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QuoteOption.sort_order",
        foreign_keys="QuoteOption.quote_id",
    )
    transport_segments: Mapped[list[QuoteTransportSegment]] = relationship(
        "QuoteTransportSegment",
        back_populates="quote",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QuoteTransportSegment.sequence",
        foreign_keys="QuoteTransportSegment.quote_id",
    )
    rejected_candidates: Mapped[list[QuoteRejectedCandidate]] = relationship(
        "QuoteRejectedCandidate",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QuoteRejectedCandidate.sort_order",
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
    # The per-option figures frozen into this version (Stage 3.4). A snapshot of
    # a multi-option quote is meaningless without them: the headline is only the
    # recommended option, and the client saw all of them.
    options: Mapped[list[QuoteVersionOption]] = relationship(
        "QuoteVersionOption",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="QuoteVersionOption.sort_order",
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


# --------------------------------------------------------------------------- #
# Stage 3 — multi-option quotes.
#
# One quotation presents several priced alternatives side by side (the reference
# document offers six hotels plus a villa option, one flagged Recommended), so an
# "option" sits between the quote and its computed lines.
#
# The split respects the immutability rule from Stage 2.8: `quote_options` holds
# ASSEMBLY (which properties are offered, rooming inputs, manual fees, ordering)
# and stays editable, while every computed figure lands in a version — as JSONB
# in `quote_versions.snapshot` plus queryable rows in `quote_version_options`.
# Re-pricing appends; it never rewrites what a client was already shown.
# --------------------------------------------------------------------------- #

#: Meal-plan fallback chain, in order (design doc §3.4).
MEAL_PLAN_FALLBACK_ORDER = ("full_board", "half_board", "bed_and_breakfast")


class QuoteOption(UUIDPKMixin, TimestampMixin, Base):
    """One accommodation alternative offered within a quote."""

    __tablename__ = "quote_options"
    __table_args__ = (
        UniqueConstraint("quote_id", "accommodation_id", name="uq_quote_option_accommodation"),
    )

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    accommodation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="RESTRICT"), nullable=False
    )
    # Resolved by the engine when it picks the cheapest eligible rate within the
    # hotel, so both are NULL on a freshly assembled option.
    room_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("room_types.id", ondelete="RESTRICT"), nullable=True
    )
    meal_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="RESTRICT"), nullable=True
    )
    # ceil(pax / room_capacity); an odd single room is charged in full (§3.3).
    rooms_required: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set when the requested plan was unavailable and the chain fell back, so the
    # agent can see the option is not what the client asked for.
    meal_plan_fallback_from: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # False where the structure is not directly comparable to the other options
    # (a self-catering villa against full-board resorts, say).
    is_comparable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # -- manual, backend-only money (never named on the client document) ------ #
    # Added AFTER profit and never marked up — a pass-through (§3.6).
    agent_cover_fee: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    # Group fee per meal — one chef cooks for everyone — for BnB / B&B options
    # only. NULL on half- and full-board options.
    chef_fee_per_meal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Food cost the agent enters by hand alongside the chef fee.
    manual_meal_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote: Mapped[Quote] = relationship(
        "Quote", back_populates="options", foreign_keys=[quote_id]
    )


class QuoteRejectedCandidate(UUIDPKMixin, TimestampMixin, Base):
    """A property considered but not offered, shown to the client with a reason.

    The reference document names Diani Cottages and explains it caps at 16
    guests — evidence of due diligence, so it is recorded rather than discarded.
    ``accommodation_id`` is nullable because a candidate may never have been in
    the catalogue at all, in which case only the typed name survives.
    """

    __tablename__ = "quote_rejected_candidates"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    accommodation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # engine | manual — see REJECTION_SOURCES.
    source: Mapped[str] = mapped_column(
        String(10), default="engine", server_default=text("'engine'"), nullable=False
    )


class QuoteTransportSegment(UUIDPKMixin, TimestampMixin, Base):
    """One transport movement on a quote: a line-haul or a transfer leg.

    Distinct from ``quote_transport``, which is the Stage 2 km-and-fuel safari
    vehicle model and remains for game-drive costing. A rail line-haul must be
    accompanied by transfer segments (pickup to terminus, terminus to hotel, and
    the reverse); a quote missing them is under-priced, and validation rejects it
    rather than quietly absorbing the cost.
    """

    __tablename__ = "quote_transport_segments"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # line_haul | transfer
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # road | rail — never air, which Heissal cannot ticket.
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    travel_class: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    # Which destination tariff table this segment prices against.
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="RESTRICT"), nullable=True
    )
    # Set for road segments run on our own or hired fleet.
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=True
    )
    vehicle_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Tickets, vehicles or legs, depending on the tariff cost basis.
    units: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # VVIP upgrades are an optional client-facing extra, not part of the package.
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_vvip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    quote: Mapped[Quote] = relationship(
        "Quote", back_populates="transport_segments", foreign_keys=[quote_id]
    )


class QuoteVersionOption(UUIDPKMixin, Base):
    """Per-option computed money, frozen into a version.

    Kept as a table rather than only inside ``snapshot`` so the CRM can answer
    "which option did the client pick, and what were the others priced at"
    without unpacking JSONB. Immutable, like the version that owns it: figures are
    denormalised (accommodation name, meal plan label) so the row still reads
    correctly after a property is renamed or a rate changes.
    """

    __tablename__ = "quote_version_options"
    __table_args__ = (UniqueConstraint("version_id", "option_id", name="uq_version_option"),)

    version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_versions.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    # SET NULL: deleting an option must not erase what was quoted for it.
    option_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_options.id", ondelete="SET NULL"), nullable=True
    )
    accommodation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accommodations.id", ondelete="SET NULL"), nullable=True
    )
    accommodation_name: Mapped[str] = mapped_column(String(200), nullable=False)
    meal_plan_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rooms_required: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Backend-only build-up (§3.6), all VAT-inclusive.
    cost_subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    contingency_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    profit_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    agent_cover_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # What Heissal actually pays suppliers, and the half-discount it keeps —
    # without these two the margin report understates itself (§3.5).
    supplier_paid_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    retained_discount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Client-facing figures.
    selling_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # NULL when the group is not uniform (mixed traveller types or mixed
    # residency), in which case only the total is shown (§3.6, §3.6a).
    per_person: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_comparable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
