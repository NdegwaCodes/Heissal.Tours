"""Proposal copy, and the review it has to pass (§4.4).

A row here is one piece of proposed writing about a property or a destination,
in one of three states: drafted, approved, or rejected. Only an **approved** one
can reach a client document, and approval is its own permission — the same
split as issuing a quotation (§3.4), and for the same reason: composing
something and putting it in front of a client are different levels of trust.

**History is kept, not overwritten.** A rejected draft is the record of what was
nearly sent, and a superseded one is what an old proposal actually said. Both are
worth more than the row they would replace: a client asking why this year's
description differs from last year's is asking a question the table can answer.

Whether the words came from a model or from an agent is on the row (``provider``)
rather than implied by which endpoint wrote it. Not because one is better — an
agent who has stayed at the property writes the better paragraph — but because
only one of them can be asked what it meant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: A draft is proposed, an approved one may be printed, a rejected one is kept
#: as the record of what was nearly sent.
DRAFT = "draft"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES = (DRAFT, APPROVED, REJECTED)


class Narrative(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "narratives"
    __table_args__ = (
        CheckConstraint(
            "status in ('draft', 'approved', 'rejected')",
            name="ck_narrative_status",
        ),
        CheckConstraint(
            "subject in ('accommodation', 'destination')",
            name="ck_narrative_subject",
        ),
        # The lookup the document makes on every option page: the approved copy
        # for this subject, newest first.
        Index("ix_narrative_subject_status", "subject", "subject_id", "status"),
    )

    #: accommodation | destination. Not two tables and not two nullable foreign
    #: keys: the review pipeline is identical for both, and a second copy of it
    #: is a second place for the approval gate to be got wrong.
    subject: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The property or destination this is about. Deliberately NOT a foreign
    #: key — it points at one of two tables — so deletion is handled by the
    #: service rather than by the database.
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=DRAFT, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which provider produced it, and which model where there was one. Stored
    #: rather than inferred, so a sentence on a two-year-old proposal can still
    #: be attributed (§3.12's instinct, applied to words instead of money).
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The facts the provider was given. Frozen beside the text because a
    #: description written from a brief listing full board is defensible and one
    #: written from nothing is not.
    brief: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Whoever asked for it, and whoever approved or rejected it. Two people
    #: rather than one on purpose: an agent generating their own copy and
    #: approving it in the same breath is the failure this gate exists to stop,
    #: and a record with both names is what makes that visible.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why it was rejected, for whoever writes the next one.
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set when a later narrative was approved over this one. The row stays: it
    #: is what an already-issued proposal actually said.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_printable(self) -> bool:
        """Whether a client document may use this. Approved and not superseded."""
        return self.status == APPROVED and self.superseded_at is None

