"""Issuing, resolving and revoking a client's access (§7.2).

The rules are in :mod:`app.modules.portal.view`; this is the half that talks to
the database. Four things worth reading before changing anything here.

**The plaintext token exists for one function call.** It is generated, hashed,
stored as the hash, and returned to the caller once. There is no second way to
get it, and that is the point: a table of live links is a table of credentials,
and the agent who needs to resend one can issue a new grant in the same click.

**Resolution is by hash, in one indexed lookup.** No listing, no comparison
loop, and no brute-force lockout — 256 bits of randomness needs no rate limit,
and a lockout on an unauthenticated endpoint keyed on a token is a way for a
stranger to lock a client out of their own itinerary.

**The trip is built from the frozen snapshot, never re-priced.** §7.1 books
against the version for a reason and this is the other end of it: what the
client sees is what they agreed to.

**Nothing here writes anything about the booking.** The portal has no write
endpoint at all, so there is nothing to authorise — the read-only guarantee is
the absence of code rather than a check that could be got wrong. The one thing
a client's visit does write is the grant's own "last seen".
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, NotFoundError
from app.core.security import hash_refresh_token
from app.modules.bookings.models import CANCELLED as BOOKING_CANCELLED
from app.modules.bookings.models import Booking
from app.modules.bookings.schedule import Owed
from app.modules.bookings.service import BookingService
from app.modules.portal.models import BookingAccessGrant
from app.modules.portal.view import (
    AccessRefused,
    Grant,
    Trip,
    check_access,
    default_expiry,
    trip_of,
)
from app.modules.quotes.models import QuoteVersion


def new_token() -> str:
    """A 256-bit opaque access token.

    ``token_urlsafe(32)`` rather than a UUID: a UUID has 122 bits and looks
    like an identifier, which invites somebody to try incrementing one.
    """
    return secrets.token_urlsafe(32)


class PortalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- issuing -------------------------------------------------------------- #

    async def issue(
        self,
        booking_id: uuid.UUID,
        *,
        label: str | None = None,
        expires_on: date | None = None,
        actor_id: uuid.UUID | None = None,
        today: date | None = None,
    ) -> tuple[BookingAccessGrant, str, str]:
        """A new grant, plus the token and the link — both returned **once**.

        A cancelled booking cannot be given a link. Not because the rules could
        not cope — :func:`check_access` would refuse it on use — but because
        sending a client a link that opens onto "this has been cancelled" is a
        worse way to have that conversation than a telephone call.
        """
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        if booking.status == BOOKING_CANCELLED:
            raise AppError(
                f"Booking {booking.reference} has been cancelled, so there is "
                f"no trip for a link to open. That conversation is a phone "
                f"call, not a link."
            )
        when = today or date.today()
        last_day = expires_on or default_expiry(
            booking.departure_date,
            after_days=settings.PORTAL_ACCESS_DAYS_AFTER_TRAVEL,
            today=when,
        )
        if last_day < when:
            raise AppError(
                "A link cannot expire in the past — it would never work once."
            )

        token = new_token()
        grant = BookingAccessGrant(
            booking_id=booking.id,
            token_hash=hash_refresh_token(token),
            label=(label or "").strip() or None,
            expires_on=last_day,
            created_by=actor_id,
        )
        self.db.add(grant)
        await self.db.commit()
        await self.db.refresh(grant)
        return grant, token, link_for(token)

    async def grants(self, booking_id: uuid.UUID) -> list[BookingAccessGrant]:
        """Every grant on a booking, newest first. Never the tokens."""
        return list(
            (
                await self.db.execute(
                    select(BookingAccessGrant)
                    .where(BookingAccessGrant.booking_id == booking_id)
                    .order_by(BookingAccessGrant.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def revoke(
        self,
        grant_id: uuid.UUID,
        *,
        reason: str,
        actor_id: uuid.UUID | None = None,
    ) -> BookingAccessGrant:
        """Withdraw a link. A reason is required, and it stays on the row.

        Because a link gets forwarded into a family group chat, and the next
        agent looking at the booking needs to know whether that is why this one
        is dead — the §5.3 argument about a voided log entry.
        """
        grant = await self.db.get(BookingAccessGrant, grant_id)
        if grant is None:
            raise NotFoundError("Access link not found.")
        if not reason.strip():
            raise AppError(
                "Say why this link is being withdrawn. Without a reason the "
                "next person cannot tell a leak from a mistake."
            )
        if grant.is_revoked:
            raise AppError("This link has already been withdrawn.")
        grant.revoked_at = datetime.now(UTC)
        grant.revoked_by = actor_id
        grant.revoke_reason = reason.strip()
        await self.db.commit()
        await self.db.refresh(grant)
        return grant

    # -- resolving ------------------------------------------------------------ #

    async def resolve(
        self, token: str, *, today: date | None = None, record_visit: bool = True
    ) -> tuple[BookingAccessGrant, Booking]:
        """The grant and booking behind a token, or a refusal a client can read.

        One indexed lookup on the hash. A token that matches nothing gets the
        same wording as one that has expired, deliberately: the alternative
        tells a stranger holding a guessed string whether they got the shape
        right.
        """
        when = today or date.today()
        grant = (
            await self.db.execute(
                select(BookingAccessGrant).where(
                    BookingAccessGrant.token_hash == hash_refresh_token(token or "")
                )
            )
        ).scalar_one_or_none()
        if grant is None:
            raise AccessRefused(
                "portal_link_unknown",
                "This link does not work. Ask your consultant to send a fresh "
                "one — nothing about your booking has changed.",
            )
        booking = await self.db.get(Booking, grant.booking_id)
        if booking is None:
            raise AccessRefused(
                "portal_link_unknown",
                "This link does not work. Ask your consultant to send a fresh "
                "one.",
            )
        check_access(
            Grant(
                expires_on=grant.expires_on,
                revoked=grant.is_revoked,
                revoke_reason=grant.revoke_reason,
                booking_status=booking.status,
                booking_reference=booking.reference,
            ),
            today=when,
        )
        if record_visit:
            # The only thing a client's visit writes. Committed here rather
            # than left to the request's end so that a later failure rendering
            # the page does not lose the fact that they looked.
            grant.last_seen_at = datetime.now(UTC)
            grant.view_count = (grant.view_count or 0) + 1
            await self.db.commit()
            await self.db.refresh(grant)
        return grant, booking

    # -- what a client sees --------------------------------------------------- #

    async def trip(self, booking: Booking) -> Trip:
        """The booked trip, from the version the client accepted.

        The dates, the headcount and the money come off the **booking**, which
        §7.1 froze for exactly this reason; the itinerary, the property and the
        inclusions come out of the version's snapshot through the allow-list in
        :mod:`app.modules.portal.view`.
        """
        version = await self.db.get(QuoteVersion, booking.quote_version_id)
        snapshot = version.snapshot if version is not None else {}
        return trip_of(
            snapshot if isinstance(snapshot, dict) else {},
            option_id=str(booking.option_id) if booking.option_id else None,
            reference=booking.reference,
            status=booking.status,
            arrival=booking.arrival_date,
            departure=booking.departure_date,
            pax_count=booking.pax_count,
            total=booking.total_amount,
            currency=booking.currency,
        )

    async def statement(
        self, booking: Booking, *, today: date | None = None
    ) -> Owed:
        """What is paid and what is owed — the page a client actually opens.

        Straight through to §7.1's ``position``: one arithmetic for the
        statement an operator reads and the one a client reads, because two
        would eventually disagree and the client's copy would be the one in
        somebody's inbox.
        """
        return await BookingService(self.db).position(booking.id, today=today)


def link_for(token: str) -> str:
    """The link an agent sends.

    The token is in the **fragment**. Browsers do not send a fragment to a
    server, so it stays out of access logs and out of the Referer header when
    the client clicks through to an airline or a hotel from their itinerary.
    The portal app reads it out of the fragment and sends it as a bearer token.
    """
    return f"{settings.PORTAL_BASE_URL.rstrip('/')}/trip#{token}"


__all__ = ["PortalService", "link_for", "new_token"]
