"""The record of what was said to whom, and when (§5.3).

One row is one call, email, message, meeting or internal note. The rules that
read it are in :mod:`app.modules.comms.rules`; three decisions shape the table
itself.

**It is a log, not a mailbox.** Nothing here sends anything. Sending mail from
the platform means a provider, a domain that passes SPF and DKIM, and a
mailbox somebody actually reads the replies in — none of which exists yet, and
a half-built one that silently fails to deliver a quotation is worse than an
agent using Gmail and typing what they sent. ``external_ref`` is the seam: when
there is a provider, its message id lands there and the rows do not change.

**It attaches to whatever the conversation was about.** A lead, yes — but the
talking does not stop when a lead is won, and a log that only knew about leads
would lose every word exchanged about the quote, the booking and the trip. So
``subject``/``subject_id`` as in §4.4's narratives: one table, one shape, and a
lead's timeline gathers its client's, its quotes' and its bookings' entries.

**Nothing is edited away.** A wrong entry is amended (and says it was) or
voided with a reason (and stays visible, counting towards nothing). There is no
delete: "we called on the 4th" is either a fact or a record of what somebody
believed, and both are worth more than a blank.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: What a conversation can be about. ``lead`` is the common one; the rest exist
#: because a client rings up about a booking eight months after the lead that
#: produced it was closed, and that call belongs to the booking.
LEAD = "lead"
CLIENT = "client"
QUOTE = "quote"
BOOKING = "booking"
SUBJECTS = (LEAD, CLIENT, QUOTE, BOOKING)


class Communication(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "communications"
    __table_args__ = (
        CheckConstraint(
            "subject in ('lead', 'client', 'quote', 'booking')",
            name="ck_communication_subject",
        ),
        CheckConstraint(
            "direction in ('inbound', 'outbound', 'internal')",
            name="ck_communication_direction",
        ),
        # A length belongs to something that has one, and never to an attempt
        # that did not connect. Enforced here as well as in the rules because
        # a nineteen-minute unanswered call is the kind of nonsense that gets
        # into a database through a script rather than through the API.
        CheckConstraint(
            "duration_minutes is null or (duration_minutes > 0 and reached is not false)",
            name="ck_communication_duration",
        ),
        # The query the timeline makes: everything about this subject, newest
        # first.
        Index("ix_communication_subject", "subject", "subject_id", "occurred_at"),
    )

    #: One of :data:`SUBJECTS`. Deliberately not four nullable foreign keys:
    #: the log is one shape whatever it is about, and four columns would mean
    #: four ways for a row to belong to nothing.
    subject: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Not a foreign key — it points at one of four tables — so the service
    #: checks that the row exists and deletion is its problem, exactly as in
    #: §4.4.
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    #: Free text, normalised on the way in. See the rules module for why this
    #: is not an enum and the direction is.
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)

    #: **When it happened**, which is not when it was typed. Almost everything
    #: here is logged after the fact — between calls, at the end of the day —
    #: and ``created_at`` from the mixin holds the typing. Measuring a response
    #: time against the typing would flatter every agent who writes their notes
    #: up promptly.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    #: The email subject, or what the call was about in six words. Optional:
    #: demanding a title for a two-line note is how notes stop being written.
    subject_line: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: What was said. Required — see ``check_logged`` for why an entry with no
    #: words is worse than no entry at all.
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Did it connect? NULL where the question does not apply.
    reached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The id in whatever system actually carried it — a provider message id, a
    #: PBX call reference. The seam an integration lands on, and the thing that
    #: makes a log entry verifiable against somebody else's records.
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    logged_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # -- corrections, which are recorded rather than invisible ---------------- #
    #: Set when somebody fixed the wording, the date or the length. The entry
    #: does not say what it used to say — that would be a second history table
    #: for a log — but it does say it was changed, which is the part that
    #: matters when a figure derived from it is questioned.
    amended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amended_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Voided rather than deleted: the call logged against the wrong lead is
    #: still the record of what somebody believed had happened. A voided entry
    #: shows in the timeline, marked, and counts towards nothing — not the last
    #: contact, not the response time, not the chase count.
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None
