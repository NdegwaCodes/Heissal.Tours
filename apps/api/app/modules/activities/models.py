"""Activities catalogue and their residence-tiered rates.

An activity (game drive, boat ride, balloon safari …) may belong to a
destination and/or supplier. Rates are effective-dated and per residence
category, with adult/child prices. A missing rate is an explicit error.
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class Activity(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "activities"

    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # -- Stage 3 --------------------------------------------------------------- #
    # Mandatory activities are costed into the package and listed under the
    # document's Included section; optional ones are priced per person alongside.
    # server_default so the NOT NULL add can backfill the existing rows.
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Some experiences warrant a full section of their own in the quotation (the
    # reference document gives the Wasini Island excursion a whole page).
    has_own_section: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )


class ActivityRate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "activity_rates"
    __table_args__ = (
        UniqueConstraint(
            "activity_id",
            "residence_category_id",
            "effective_from",
            name="uq_activity_rate_period",
        ),
    )

    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), index=True,
        nullable=False,
    )
    residence_category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("residence_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    adult_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    child_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ActivityPriceTier(UUIDPKMixin, TimestampMixin, Base):
    """A priced variant of one activity — typically a duration (Stage 3.1).

    Timed experiences are sold as a ladder rather than a single price: quad biking
    at 10, 15 or 30 minutes, each costing something different. The quotation
    renders these as a small table under the activity so the client can choose.

    Carries its own ``residence_category_id`` because the resident / non-resident
    gap applies to activity fees just as it does to hotel rates and park fees, and
    its own VAT basis for the same reason every other rate does.
    """

    __tablename__ = "activity_price_tiers"
    __table_args__ = (
        UniqueConstraint(
            "activity_id",
            "residence_category_id",
            "label",
            "effective_from",
            name="uq_activity_price_tier_period",
        ),
    )

    activity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("activities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    residence_category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("residence_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # What the client reads, e.g. "10 minutes" or "Two-seater".
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Priced per person, like every optional extra on the document.
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    vat_inclusive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vat_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("16"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
