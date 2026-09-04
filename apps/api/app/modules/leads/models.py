"""Leads and the pipeline they move through (§5.2).

§5.1 gave the business an outcome for every quote. This is the half in front of
it: the enquiry that arrives before anybody prices anything, who owns it, what
stage it is at, and — the field that decides whether a CRM is used or abandoned
— **what the next action is and when**.

Three decisions shape the tables.

**The stages are configuration, not code.** Heissal has not told us their sales
stages, and a `CHECK` constraint listing mine would need a migration the first
time somebody wants "site inspection" between quoted and negotiating. So the
stages are rows: ordered, renameable, with flags saying which one means won and
which mean lost. A generic set is seeded and is theirs to change.

**Every move is recorded.** ``lead_stage_events`` is where the pipeline stops
being a status column and becomes something worth reading: how long a lead sits
at each stage, and which stage they die at. A current-stage-only design can
tell you that eleven leads are at "quoted" and never that they have been there
for two months.

**A lead may precede a client.** An enquiry arrives as a name and a phone
number; the client record is created when there is something to quote. So
``client_id`` is nullable and the contact fields sit on the lead — with the
client winning once it exists, since that is the record the invoice uses.
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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: Where an enquiry came from. A conventional set rather than a constraint:
#: sources multiply with every campaign, and a lead refused because "instagram"
#: is not in an enum is a lead somebody records as "other" and stops trusting.
#: Normalised on the way in so "Website " and "website" are one source in a
#: report.
COMMON_SOURCES = (
    "website",
    "referral",
    "repeat_client",
    "walk_in",
    "phone",
    "email",
    "agent",
    "social",
    "other",
)


class LeadStage(UUIDPKMixin, TimestampMixin, Base):
    """One stage of the sales pipeline. Reference data, edited by the client.

    ``is_won`` and ``is_lost`` are what the analytics read, so renaming a stage
    cannot break a report: the funnel asks "which stage means won" rather than
    comparing against a string it was compiled with.
    """

    __tablename__ = "lead_stages"
    __table_args__ = (
        UniqueConstraint("key", name="uq_lead_stage_key"),
        Index("ix_lead_stage_order", "sort_order"),
    )

    #: A stable handle for seeding and for tests. The *name* is what an agent
    #: sees and is theirs to change; the key stays.
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Where a new enquiry lands. Exactly one stage should have it; the service
    #: enforces that rather than the database, so re-ordering the pipeline is
    #: one call and not a transaction puzzle.
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    is_won: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    is_lost: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def is_terminal(self) -> bool:
        return self.is_won or self.is_lost


class Lead(UUIDPKMixin, TimestampMixin, Base):
    """One enquiry, from arrival to won or lost."""

    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_lead_stage_owner", "stage_id", "owner_id"),
        # The query the morning starts with: what is due, oldest first.
        Index("ix_lead_next_action", "next_action_on"),
    )

    #: Free text, normalised. See ``COMMON_SOURCES`` for why it is not an enum.
    source: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    #: The campaign, referrer or agent behind the source — "wedding fair",
    #: "Jane at Acme". The pair (source, detail) is what a marketing spend
    #: question actually needs; a source alone says only "the internet".
    source_detail: Mapped[str | None] = mapped_column(String(160), nullable=True)

    #: Set once there is something to quote. Until then the contact fields
    #: below are all we have, and a lead is not worth blocking on a client
    #: record nobody has typed yet.
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("lead_stages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: When the lead entered its current stage, so "how long has this been
    #: sitting here" costs no join. The full history is in the events table.
    stage_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Whose lead it is. Nullable because an enquiry can arrive before anybody
    #: picks it up — and an unowned lead is exactly what a queue is for.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # -- what they are asking for ------------------------------------------- #
    # Loose on purpose: an enquiry is "somewhere on the coast in August, maybe
    # six of us". Pinning it to a destination id and exact dates would make the
    # form refuse the enquiry as it actually arrives.
    destination_interest: Mapped[str | None] = mapped_column(String(200), nullable=True)
    travel_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    travel_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    pax_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: What they have said they will spend, in their own currency. Money is
    #: NUMERIC plus a currency code here as everywhere else, even for a figure
    #: this soft.
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # -- the next step ------------------------------------------------------ #
    # The single most useful field in any CRM, and the reason this is on the
    # lead rather than in a task table: a lead with no next action is a lead
    # that dies quietly, and one query has to be able to find every one of them.
    next_action_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_action_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Why a lost lead was lost, in the words of whoever closed it. The stage
    #: says "lost"; this says whether we were expensive, slow, or unlucky.
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    stage: Mapped[LeadStage] = relationship("LeadStage", lazy="selectin")
    events: Mapped[list[LeadStageEvent]] = relationship(
        "LeadStageEvent",
        back_populates="lead",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="LeadStageEvent.at",
    )


class LeadStageEvent(UUIDPKMixin, Base):
    """One move through the pipeline.

    Where a status column becomes a pipeline. "Eleven leads at quoted" is a
    number; "eleven leads at quoted, average nineteen days, four of them past
    thirty" is a morning's work — and only the history can say the second.

    Backwards moves are recorded rather than refused: a deal cools, a client
    goes quiet and comes back, and a pipeline that only goes forwards is one
    where agents park leads at a stage they have left.
    """

    __tablename__ = "lead_stage_events"
    __table_args__ = (Index("ix_lead_stage_event_lead", "lead_id", "at"),)

    lead_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL on the first event, which is the lead arriving.
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lead_stages.id", ondelete="SET NULL"),
        nullable=True,
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lead_stages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    lead: Mapped[Lead] = relationship("Lead", back_populates="events")
