"""How a client gets at their own trip (§7.2).

§7.1 gave an accepted quote somewhere to go. This is the other half of Stage 7:
the client's own view of what they booked — the itinerary, the statement, the
document they agreed to.

**Clients are not users.** There is no client password, no registration and no
reset, and that is a decision rather than a shortcut. A `users` row carries
roles and permissions into every guard in the system, and a client belongs on
the other side of that boundary. More practically: somebody books one trip
every year or two, so a password is a thing they will have forgotten by the
time they need it — the login-support cost is real and the thing being
protected is one trip's itinerary, which a link protects just as well.

So access is a **grant**: one high-entropy token, for one booking, that an agent
sends by hand (nothing here sends anything — see §5.3). Three properties make
that safe enough for what it guards:

* the token is stored **hashed**, so a copy of this table is not a set of live
  links, and the plaintext is returned exactly once at creation;
* it is scoped to **one booking**, not to a client, so a link forwarded into a
  family WhatsApp group exposes one trip rather than a relationship;
* it can be **revoked**, because that forwarding is not hypothetical.

And the token travels in the link's **fragment** (``…/trip#<token>``), which a
browser never sends to a server: not in an access log, not in a Referer header
when the client clicks through to a supplier's site. The portal app reads it
from the fragment and sends it as a bearer token.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin
from app.modules.bookings.models import Booking


class BookingAccessGrant(UUIDPKMixin, TimestampMixin, Base):
    """One client's read-only access to one booking."""

    __tablename__ = "booking_access_grants"
    __table_args__ = (
        # The lookup every portal request makes, and the reason the hash is
        # unique: two grants cannot collide, and a duplicate would mean one of
        # them is unreachable.
        UniqueConstraint("token_hash", name="uq_booking_access_token"),
        Index("ix_booking_access_booking", "booking_id"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: SHA-256 of the token, never the token. Not bcrypt: a work factor exists
    #: to make guessing a *low*-entropy secret expensive, and this is 256 bits
    #: of randomness — there is nothing to slow down. (bcrypt would also
    #: silently truncate it at 72 bytes, and it has to be looked up by value.)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Who it was sent to, in the agent's words — "Mrs Achieng", "the group's
    #: organiser". A booking can have several: one for the person paying and
    #: one for the person actually travelling, and revoking one should not lock
    #: the other out.
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)

    #: The last day it works. Defaulted to a while after they travel rather
    #: than to departure: the statement, the receipts and the itinerary are all
    #: wanted after the fact.
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Why. A revoked link with no reason leaves the next agent wondering
    #: whether to issue another one — the §5.3 argument about a voided entry.
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Whether the client has ever opened it, and when they last did. A real
    #: sales question — "did they even see the itinerary?" — and the cheapest
    #: sign that a link has ended up somewhere it should not be.
    #:
    #: Two columns rather than an access log: a row per page view would grow
    #: without bound and answer nothing the pair does not, and it is not
    #: evidence of anything in a dispute either, since a forwarded link is
    #: indistinguishable from the client's own browser.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    booking: Mapped[Booking] = relationship("Booking", lazy="selectin")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
