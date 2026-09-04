"""The client portal, and the links that open it (§7.2).

Two halves with two different callers.

``/bookings/{id}/portal-links`` is staff: issue a link, list them, withdraw
one. ``portal:manage`` is its own permission, because handing somebody a
credential is not the same act as editing a booking.

``/portal/*`` is the client, holding a grant token rather than a login. Those
endpoints are **read-only by construction** — there is no write endpoint here
at all, so there is nothing a grant could be tricked into authorising, and the
guarantee is the absence of code rather than a check somebody has to maintain.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.bookings.models import Booking
from app.modules.documents.service import QuotationDocumentService
from app.modules.portal.schemas import (
    DayRead,
    GrantCreate,
    GrantIssued,
    GrantRead,
    GrantRevoke,
    InstalmentRead,
    MovementRead,
    PaymentRead,
    StatementRead,
    StayRead,
    TripRead,
)
from app.modules.portal.service import PortalService
from app.modules.portal.view import AccessRefused
from app.modules.users.models import User

router = APIRouter(tags=["portal"])

#: Its own permission. Issuing a link hands a credential to somebody outside
#: the business; editing a booking does not.
MANAGE = "portal:manage"


class PortalAccessDenied(AppError):
    """A grant that will not open a trip, as an HTTP response.

    403 rather than 401: there is nothing to log in to, so "authenticate and
    try again" would be advice a client cannot follow. The message is the one
    the rules wrote — a client reading "this link no longer works" reaches for
    the telephone with nothing to say.
    """

    status_code = 403

    def __init__(self, code: str, message: str):
        self.code = code.upper()
        super().__init__(message)


async def portal_access(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> tuple[uuid.UUID, Booking]:
    """Resolve a client's grant token into the booking it opens.

    The token arrives as a bearer, the same transport as a staff login, so
    there is one convention and no token in a URL path or query string. The
    link the client clicked carries it in the **fragment**, which a browser
    never sends to a server; the portal app lifts it out and sends it here.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise PortalAccessDenied(
            "portal_no_link",
            "Open your trip using the link your consultant sent you.",
        )
    try:
        grant, booking = await PortalService(db).resolve(token)
    except AccessRefused as exc:
        raise PortalAccessDenied(exc.code, exc.message) from exc
    return grant.id, booking


# --------------------------------------------------------------------------- #
# Staff: issuing and withdrawing links
# --------------------------------------------------------------------------- #


@router.post(
    "/bookings/{booking_id}/portal-links",
    response_model=GrantIssued,
    status_code=201,
)
async def issue_link(
    booking_id: uuid.UUID,
    body: GrantCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Issue a client a read-only link to their own trip.

    **This response is the only place the token ever appears.** The table holds
    a SHA-256 of it, so a copy of the database is not a set of live links, and
    an agent who needs to resend issues a new grant — one click, and separately
    revocable, which is what you want when the first one has been forwarded
    into a group chat.

    Nothing is sent. As with §5.3, the platform has no mailbox: the agent
    copies the link into whatever they are already using to talk to the client.
    """
    grant, token, url = await PortalService(db).issue(
        booking_id,
        label=body.label,
        expires_on=body.expires_on,
        actor_id=actor.id,
    )
    return GrantIssued(
        **GrantRead.model_validate(grant).model_dump(), token=token, url=url
    )


@router.get(
    "/bookings/{booking_id}/portal-links", response_model=list[GrantRead]
)
async def list_links(
    booking_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    """Every link on this booking, newest first, and whether it has been opened.

    Without the tokens, which is not an oversight — see the issue endpoint.
    """
    return await PortalService(db).grants(booking_id)


@router.post("/portal-links/{grant_id}/revoke", response_model=GrantRead)
async def revoke_link(
    grant_id: uuid.UUID,
    body: GrantRevoke,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Withdraw a link, with a reason that stays on the row.

    The reason is required because the next agent looking at this booking has
    to be able to tell a leak from a mistake — and because a client ringing to
    ask why their link stopped working deserves an answer.
    """
    return await PortalService(db).revoke(
        grant_id, reason=body.reason, actor_id=actor.id
    )


# --------------------------------------------------------------------------- #
# The client's own three pages
# --------------------------------------------------------------------------- #


@router.get("/portal/trip", response_model=TripRead)
async def my_trip(
    access: tuple[uuid.UUID, Booking] = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
):
    """The trip they booked: dates, property, day-by-day, what is included.

    Built from the **frozen version** they accepted (§3.4), never re-priced —
    the same principle §7.1 books against. The money comes off the booking,
    where it was frozen, rather than out of the snapshot.

    No cost and no margin appear, and not because they are stripped out: the
    view is assembled from an allow-list, so a costing field added to the
    snapshot next month cannot leak through this endpoint by default.
    """
    _grant_id, booking = access
    return _trip(await PortalService(db).trip(booking))


@router.get("/portal/statement", response_model=StatementRead)
async def my_statement(
    access: tuple[uuid.UUID, Booking] = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
):
    """What is paid, what is due, and when — the page a client actually opens.

    The same arithmetic as the operator's view (§7.1), deliberately: two would
    eventually disagree, and the client's copy is the one sitting in somebody's
    inbox.
    """
    _grant_id, booking = access
    owed = await PortalService(db).statement(booking)
    return StatementRead(
        reference=booking.reference,
        currency=owed.currency,
        total=owed.total,
        paid=owed.paid,
        balance=owed.balance,
        overpaid=owed.overpaid,
        is_settled=owed.is_settled,
        schedule=[
            InstalmentRead(
                label=row.label,
                due_on=row.due_on,
                amount=row.amount,
                currency=owed.currency,
            )
            for row in sorted(booking.instalments, key=lambda one: one.sort_order)
        ],
        payments=[
            PaymentRead(
                amount=row.amount,
                currency=row.currency,
                paid_on=row.paid_on,
                method=row.method,
                reference=row.reference,
            )
            for row in sorted(booking.payments, key=lambda one: one.paid_on)
        ],
        overdue=[
            InstalmentRead(
                label=row.label,
                due_on=row.due_on,
                amount=row.amount,
                currency=owed.currency,
            )
            for row in owed.overdue
        ],
        next_due=(
            InstalmentRead(
                label=owed.next_due.label,
                due_on=owed.next_due.due_on,
                amount=owed.next_due.amount,
                currency=owed.currency,
            )
            if owed.next_due
            else None
        ),
    )


@router.get("/portal/document.html", response_class=HTMLResponse)
async def my_document(
    access: tuple[uuid.UUID, Booking] = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
):
    """The proposal they accepted, exactly as it was issued.

    The same renderer and the same frozen version as §3.11, pinned to the
    booking's own ``quote_version_id``: a client who re-opens their document in
    March must see the document they agreed to in September, not a re-render of
    a quote that has been edited since.
    """
    _grant_id, booking = access
    version_number = await _version_number(db, booking)
    html = await QuotationDocumentService(db).render_html(
        booking.quote_id, version_number=version_number, inline_assets=True
    )
    return HTMLResponse(content=html)


async def _version_number(db: AsyncSession, booking: Booking) -> int | None:
    from app.modules.quotes.models import QuoteVersion

    version = await db.get(QuoteVersion, booking.quote_version_id)
    return version.version_number if version is not None else None


def _trip(trip) -> TripRead:
    return TripRead(
        reference=trip.reference,
        status=trip.status,
        arrival_date=trip.arrival_date,
        departure_date=trip.departure_date,
        pax_count=trip.pax_count,
        total=trip.total,
        currency=trip.currency,
        property_name=trip.property_name,
        room_type=trip.room_type,
        board=trip.board,
        nights=trip.nights,
        description=trip.description,
        stays=[StayRead(**vars(one)) for one in trip.stays],
        days=[
            DayRead(
                number=one.number,
                on=one.on,
                destination=one.destination,
                property_name=one.property_name,
                board=one.board,
                movements=[MovementRead(**vars(move)) for move in one.movements],
                excursions=one.excursions,
                is_arrival=one.is_arrival,
                is_departure=one.is_departure,
                has_night=one.has_night,
            )
            for one in trip.days
        ],
        included=trip.included,
    )
