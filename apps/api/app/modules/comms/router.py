"""The contact log over the API (§5.3).

Two permissions, and no third. Reading the log and writing to it are different
levels of trust; **editing** it is not a level of trust the design offers at
all, beyond an amendment that says it happened and a void that keeps the row.
See the service for why.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.comms.models import LEAD
from app.modules.comms.rules import first_response_hours, silence
from app.modules.comms.schemas import (
    CommunicationAmend,
    CommunicationLog,
    CommunicationRead,
    CommunicationVoid,
    ContactedRead,
    SilenceRead,
    TimelineRead,
)
from app.modules.comms.service import CommsService, as_contact, normalise_subject
from app.modules.leads.models import Lead
from app.modules.users.models import User

router = APIRouter(tags=["communications"])

READ = "comm:read"
LOG = "comm:log"
#: Its own permission. Amending or voiding an entry changes figures somebody
#: has already reported — a response time, a chase count, when the client was
#: last spoken to — and that is a different act from recording a call.
AMEND = "comm:amend"


@router.post(
    "/{subject}/{subject_id}/communications",
    response_model=CommunicationRead,
    status_code=201,
)
async def log_communication(
    subject: str,
    subject_id: uuid.UUID,
    body: CommunicationLog,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(LOG)),
):
    """Log a call, email, message, meeting or note.

    ``subject`` is one of ``leads``, ``clients``, ``quotes`` or ``bookings``,
    so the path reads as the rest of the API does. A conversation about a quote
    still counts as contact with that quote's lead — an agent who logs it in
    the obvious place has not failed to make the call.

    Nothing is sent. This is a record of what somebody did in their own mail
    client or on their own phone; see the model for why a half-built sender is
    worse than none.
    """
    return await CommsService(db).log(
        subject=_kind(subject),
        subject_id=subject_id,
        actor_id=actor.id,
        **body.model_dump(),
    )


@router.get(
    "/{subject}/{subject_id}/communications", response_model=TimelineRead
)
async def read_timeline(
    subject: str,
    subject_id: uuid.UUID,
    after_chases: int = Query(
        default=2, ge=1, le=50, description="Attempts before silence is reported."
    ),
    after_days: int = Query(
        default=7, ge=1, le=365, description="Days of silence before it is reported."
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """Everything ever said about this, newest first, and what it adds up to.

    On a **lead** the timeline is wider than the lead's own entries: it gathers
    the client it points at, the quotes raised from it and the bookings made
    from those. One place to read the whole relationship is the only reason
    anybody opens a CRM — and a log that stopped at the lead would lose every
    word exchanged about the trip that was actually sold.

    Both silence thresholds are the caller's. Two chases in two days is a keen
    agent; two over three weeks is a client who has booked elsewhere, and no
    default here can tell them apart.
    """
    kind = _kind(subject)
    service = CommsService(db)
    rows, log = await service.timeline(kind, subject_id)
    contacts = [as_contact(row) for row in rows]

    responded = None
    if kind == LEAD:
        lead = await db.get(Lead, subject_id)
        if lead is not None:
            responded = first_response_hours(lead.created_at, contacts)

    quiet = silence(
        log,
        now=datetime.now(UTC),
        after_chases=after_chases,
        after_days=after_days,
    )
    return TimelineRead(
        subject=kind,
        subject_id=subject_id,
        entries=[CommunicationRead.model_validate(row) for row in rows],
        summary=ContactedRead(
            entries=log.entries,
            contacts=log.contacts,
            last_contact_at=log.last_contact_at,
            last_inbound_at=log.last_inbound_at,
            last_outbound_at=log.last_outbound_at,
            chases=log.chases,
            by_channel=log.by_channel,
            by_direction=log.by_direction,
            unreached_calls=log.unreached_calls,
        ),
        first_response_hours=responded,
        gone_quiet=(
            SilenceRead(
                chases=quiet.chases,
                days=quiet.days,
                since=quiet.since,
                ever_replied=quiet.ever_replied,
                message=quiet.message,
            )
            if quiet
            else None
        ),
    )


@router.patch("/communications/{comm_id}", response_model=CommunicationRead)
async def amend_communication(
    comm_id: uuid.UUID,
    body: CommunicationAmend,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(AMEND)),
):
    """Fix the wording, the date, the length or whether it connected.

    The entry records that it was amended. Not what it used to say: the
    question worth answering is "was the figure I am reading computed on these
    words", and the stamp answers it.
    """
    return await CommsService(db).amend(
        comm_id, body.model_dump(exclude_unset=True), actor_id=actor.id
    )


@router.post("/communications/{comm_id}/void", response_model=CommunicationRead)
async def void_communication(
    comm_id: uuid.UUID,
    body: CommunicationVoid,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(AMEND)),
):
    """Mark an entry as never having happened, keeping it visible.

    There is no delete. The call logged against the wrong client is still the
    record of what somebody believed, and a vanished row leaves the next person
    wondering why a response time changed. A voided entry counts towards
    nothing — not the last contact, not the response time, not the chases.
    """
    return await CommsService(db).void(comm_id, reason=body.reason, actor_id=actor.id)


@router.post("/leads/{lead_id}/recompute-contact", response_model=dict)
async def recompute_contact(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(AMEND)),
):
    """Rebuild a lead's contact stamps from the log.

    The stamps on ``leads`` are denormalised so the morning list stays one
    query. This is what makes that safe rather than a second source of truth:
    they are derivable, one call derives them, and a test proves it agrees with
    what logging maintained incrementally.
    """
    lead = await CommsService(db).recompute(lead_id)
    return {
        "lead_id": str(lead.id),
        "last_contact_at": lead.last_contact_at,
        "last_inbound_at": lead.last_inbound_at,
    }


#: The path segment as the rest of the API spells it, mapped to the subject the
#: log stores. A mapping rather than a bare string so ``/leads/{id}/…`` cannot
#: silently become a subject nothing else queries.
_PATHS = {
    "leads": "lead",
    "clients": "client",
    "quotes": "quote",
    "bookings": "booking",
}


def _kind(segment: str) -> str:
    return normalise_subject(_PATHS.get(segment.lower(), segment))
