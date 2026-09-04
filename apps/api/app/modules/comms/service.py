"""Logging what was said, and reading it back (§5.3).

The rules are in :mod:`app.modules.comms.rules`; this is the half that talks to
the database. Four things it does that are worth reading before changing.

**Logging a call can set the lead's next action in the same breath.** Because
the end of a call is the only moment somebody knows what the next step is, and
a second screen to go and set it is a step nobody takes. It reuses the lead's
own ``next_action_on`` rather than inventing a follow-up date here: two answers
to "what happens next" is one too many, which is the mistake §3.8 was built to
undo for headcounts.

**Writing an entry stamps the lead.** ``leads.last_contact_at`` and
``last_inbound_at`` are denormalised from the log so the morning list stays one
query. Nothing here writes an entry without updating them, which is why it is
one method and not two — and :meth:`CommsService.recompute` can rebuild both
from the log, which is what makes the denormalisation safe rather than a second
source of truth.

**A lead's timeline is not only the lead's entries.** The talking does not stop
when a lead is won: it moves to the quote, then the booking, then the trip. So
the timeline gathers the lead, the client it points at, the quotes raised from
it and the bookings made from those — one place to read everything ever said,
which is the only reason anybody opens a CRM.

**Nothing is sent and nothing is deleted.** See the model for both.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.bookings.models import Booking
from app.modules.clients.models import Client
from app.modules.comms.models import BOOKING, CLIENT, LEAD, QUOTE, SUBJECTS, Communication
from app.modules.comms.rules import (
    INTERNAL,
    OUTBOUND,
    Contact,
    Contacted,
    Logged,
    LogRefused,
    Silence,
    check_logged,
    first_response_hours,
    history,
    normalise_channel,
    normalise_direction,
    silence,
)
from app.modules.leads.models import Lead
from app.modules.quotes.models import Quote

#: The table each subject lives in. A mapping rather than four branches, so
#: adding a subject is a line here and the existence check cannot be forgotten
#: for one of them.
_TABLES = {LEAD: Lead, CLIENT: Client, QUOTE: Quote, BOOKING: Booking}


def normalise_subject(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in SUBJECTS:
        raise AppError(
            f"'{value}' is not something a conversation can be about. Say "
            f"{', '.join(SUBJECTS)}."
        )
    return cleaned


class CommsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- writing -------------------------------------------------------------- #

    async def log(
        self,
        *,
        subject: str,
        subject_id: uuid.UUID,
        channel: str | None,
        direction: str,
        occurred_at: datetime | None = None,
        subject_line: str | None = None,
        body: str,
        reached: bool | None = None,
        duration_minutes: int | None = None,
        external_ref: str | None = None,
        actor_id: uuid.UUID | None = None,
        next_action_on: date | None = None,
        next_action_note: str | None = None,
    ) -> Communication:
        """Record one call, email, message, meeting or note.

        ``occurred_at`` defaults to now, which is the common case — logged
        straight after the call — but is settable, because the other common
        case is Friday afternoon catching up on Tuesday. The default is not the
        same thing as the row's ``created_at``: see the model for why the two
        are kept apart.
        """
        kind = normalise_subject(subject)
        await self._must_exist(kind, subject_id)

        now = datetime.now(UTC)
        when = occurred_at or now
        if when.tzinfo is None:
            # A naive timestamp compared against an aware one raises three
            # frames down in the rules, which is a TypeError where the caller
            # deserves a sentence. Assumed UTC, as everything stored here is.
            when = when.replace(tzinfo=UTC)
        try:
            way = normalise_direction(direction)
            entry = Logged(
                channel=normalise_channel(channel),
                direction=way,
                occurred_at=when,
                body=body,
                reached=reached,
                duration_minutes=duration_minutes,
            )
            check_logged(entry, now=now)
        except LogRefused as exc:
            raise AppError(str(exc)) from exc

        row = Communication(
            subject=kind,
            subject_id=subject_id,
            channel=entry.channel,
            direction=entry.direction,
            occurred_at=when,
            subject_line=(subject_line or "").strip() or None,
            body=body.strip(),
            reached=reached,
            duration_minutes=duration_minutes,
            external_ref=(external_ref or "").strip() or None,
            logged_by=actor_id,
        )
        self.db.add(row)
        await self.db.flush()

        lead = await self._lead_behind(kind, subject_id)
        if lead is not None:
            if entry.direction != INTERNAL:
                # Max, not assignment: a Tuesday call logged on Friday must not
                # move "last contacted" backwards past the Thursday email.
                lead.last_contact_at = _later(lead.last_contact_at, when)
                if entry.direction != OUTBOUND:
                    lead.last_inbound_at = _later(lead.last_inbound_at, when)
            if next_action_on is not None:
                lead.next_action_on = next_action_on
                lead.next_action_note = next_action_note
            elif next_action_note is not None:
                lead.next_action_note = next_action_note

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def amend(
        self,
        comm_id: uuid.UUID,
        data: dict,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> Communication:
        """Fix the wording, the date, the length or whether it connected.

        Not the subject and not the direction. An entry logged against the
        wrong lead is not a typo — it is a fact about a different conversation,
        and every figure derived from it (last contact, response time, chase
        count) was computed on both leads. Void it and log it where it belongs.

        The row records that it was amended. It does not record what it used to
        say: a full history of a history table is a cost with one real payoff,
        and the payoff — "was this figure computed on the words I am reading?"
        — is answered by the stamp alone.
        """
        row = await self.get(comm_id)
        if row.is_voided:
            raise AppError(
                "This entry has been voided, so amending it would be editing "
                "the record of a mistake. Log a new one."
            )
        allowed = {
            "subject_line",
            "body",
            "occurred_at",
            "reached",
            "duration_minutes",
            "external_ref",
        }
        if extra := sorted(set(data) - allowed):
            raise AppError(
                f"These cannot be amended on a logged conversation: "
                f"{', '.join(extra)}. Void the entry and log it again."
            )
        merged = {
            "channel": row.channel,
            "direction": row.direction,
            "occurred_at": row.occurred_at,
            "body": row.body,
            "reached": row.reached,
            "duration_minutes": row.duration_minutes,
        }
        merged.update({k: v for k, v in data.items() if k in merged})
        when = merged["occurred_at"]
        if isinstance(when, datetime) and when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
            merged["occurred_at"] = when
        try:
            check_logged(
                Logged(
                    channel=str(merged["channel"]),
                    direction=str(merged["direction"]),
                    occurred_at=when,  # type: ignore[arg-type]
                    body=str(merged["body"]),
                    reached=merged["reached"],  # type: ignore[arg-type]
                    duration_minutes=merged["duration_minutes"],  # type: ignore[arg-type]
                ),
                now=datetime.now(UTC),
            )
        except LogRefused as exc:
            raise AppError(str(exc)) from exc

        for field, value in data.items():
            setattr(row, field, merged.get(field, value))
        row.amended_at = datetime.now(UTC)
        row.amended_by = actor_id
        await self.db.commit()
        # An amended date changes what "last contacted" means, and the stamps
        # are derived — so they are rebuilt rather than nudged.
        await self._restamp(row)
        await self.db.refresh(row)
        return row

    async def void(
        self,
        comm_id: uuid.UUID,
        *,
        reason: str,
        actor_id: uuid.UUID | None = None,
    ) -> Communication:
        """Mark an entry as never having happened, keeping it visible.

        The call logged against the wrong client is still the record of what
        somebody believed, and a deleted row leaves the next person wondering
        why the response time changed. A reason is required for §5.2's reason:
        "voided" is a fact nobody can act on.
        """
        row = await self.get(comm_id)
        if not reason.strip():
            raise AppError(
                "Say why this entry is being voided. It stays visible, and "
                "without a reason the next person reads it as true."
            )
        if row.is_voided:
            raise AppError("This entry has already been voided.")
        row.voided_at = datetime.now(UTC)
        row.voided_by = actor_id
        row.void_reason = reason.strip()
        await self.db.commit()
        await self._restamp(row)
        await self.db.refresh(row)
        return row

    # -- reading -------------------------------------------------------------- #

    async def get(self, comm_id: uuid.UUID) -> Communication:
        row = await self.db.get(Communication, comm_id)
        if row is None:
            raise NotFoundError("Communication not found.")
        return row

    async def entries(
        self, subject: str, subject_id: uuid.UUID
    ) -> list[Communication]:
        """One subject's own entries, newest first."""
        kind = normalise_subject(subject)
        return list(
            (
                await self.db.execute(
                    select(Communication)
                    .where(
                        Communication.subject == kind,
                        Communication.subject_id == subject_id,
                    )
                    .order_by(Communication.occurred_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def timeline(
        self, subject: str, subject_id: uuid.UUID
    ) -> tuple[list[Communication], Contacted]:
        """Everything ever said about this, newest first, and what it adds up to.

        For a **lead** this gathers more than the lead's own rows: the client it
        points at, the quotes raised from it, and the bookings made from those.
        The conversation does not stop when a lead is won, and a timeline that
        stopped there would lose every word exchanged about the trip that was
        actually sold.
        """
        kind = normalise_subject(subject)
        refs = await self._related(kind, subject_id)
        if not refs:
            return [], history([])
        clauses = [
            (Communication.subject == one_kind)
            & (Communication.subject_id.in_(list(ids)))
            for one_kind, ids in refs.items()
            if ids
        ]
        stmt = select(Communication).order_by(Communication.occurred_at.desc())
        combined = clauses[0]
        for clause in clauses[1:]:
            combined = combined | clause
        rows = list((await self.db.execute(stmt.where(combined))).scalars().all())
        return rows, history([as_contact(row) for row in rows])

    async def gone_quiet(
        self,
        subject: str,
        subject_id: uuid.UUID,
        *,
        after_chases: int = 2,
        after_days: int = 7,
    ) -> Silence | None:
        """Whether this conversation has stopped, on the caller's thresholds."""
        _rows, log = await self.timeline(subject, subject_id)
        return silence(
            log, now=datetime.now(UTC), after_chases=after_chases, after_days=after_days
        )

    async def response_hours(self, lead: Lead) -> Decimal | None:
        """Hours from this enquiry arriving to the first word back, or ``None``."""
        rows, _log = await self.timeline(LEAD, lead.id)
        return first_response_hours(
            lead.created_at, [as_contact(row) for row in rows]
        )

    async def contacts_for_leads(
        self, lead_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[Contact]]:
        """Every lead's contact history, in a fixed number of queries.

        The attention list and the pipeline report both need this for every
        open lead at once, and a per-lead call would make the morning list slow
        enough to stop being opened — which is the same reason the two stamps
        are denormalised onto ``leads``.

        A lead's own entries, its quotes' and its bookings' — but deliberately
        **not** its client's. One client has many leads, and counting a call
        about this year's trip as contact on last year's enquiry would make
        every dormant lead of a repeat client read as freshly spoken to.
        """
        ids = set(lead_ids)
        if not ids:
            return {}
        # (subject, subject_id) -> the lead it belongs to.
        owners: dict[tuple[str, uuid.UUID], uuid.UUID] = {
            (LEAD, one): one for one in ids
        }
        quotes = (
            await self.db.execute(
                select(Quote.id, Quote.lead_id).where(Quote.lead_id.in_(ids))
            )
        ).all()
        for quote_id, lead_id in quotes:
            if lead_id is not None:
                owners[(QUOTE, quote_id)] = lead_id
        quote_ids = [quote_id for quote_id, _lead in quotes]
        if quote_ids:
            bookings = (
                await self.db.execute(
                    select(Booking.id, Booking.quote_id).where(
                        Booking.quote_id.in_(quote_ids)
                    )
                )
            ).all()
            for booking_id, quote_id in bookings:
                owner = owners.get((QUOTE, quote_id))
                if owner is not None:
                    owners[(BOOKING, booking_id)] = owner

        out: dict[uuid.UUID, list[Contact]] = {one: [] for one in ids}
        # Filtered on the id alone and mapped on the pair: a UUID does not
        # collide across four tables, and one IN clause beats four.
        rows = (
            (
                await self.db.execute(
                    select(Communication).where(
                        Communication.subject_id.in_(
                            [subject_id for _kind, subject_id in owners]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            owner = owners.get((row.subject, row.subject_id))
            if owner is not None:
                out[owner].append(as_contact(row))
        return out

    async def recompute(self, lead_id: uuid.UUID) -> Lead:
        """Rebuild a lead's contact stamps from the log.

        The safety net under the denormalisation on ``leads``: the stamps are
        derived, so there has to be one call that derives them, and a test that
        proves it agrees with what :meth:`log` maintained incrementally. Also
        the repair for the entries a data import or a voided row left behind.
        """
        lead = await self.db.get(Lead, lead_id)
        if lead is None:
            raise NotFoundError("Lead not found.")
        _rows, log = await self.timeline(LEAD, lead_id)
        lead.last_contact_at = log.last_contact_at
        lead.last_inbound_at = log.last_inbound_at
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    # -- plumbing ------------------------------------------------------------- #

    async def _must_exist(self, kind: str, subject_id: uuid.UUID) -> None:
        """Refuse an entry about something that is not there.

        ``subject_id`` is not a foreign key — it points at one of four tables —
        so this is the check that stops a typo becoming a log entry nobody will
        ever see again.
        """
        row = await self.db.get(_TABLES[kind], subject_id)
        if row is None:
            raise NotFoundError(f"There is no {kind} with that id to log against.")

    async def _related(
        self, kind: str, subject_id: uuid.UUID
    ) -> dict[str, set[uuid.UUID]]:
        """Every subject whose entries belong on this one's timeline."""
        refs: dict[str, set[uuid.UUID]] = {kind: {subject_id}}
        if kind != LEAD:
            return refs
        lead = await self.db.get(Lead, subject_id)
        if lead is None:
            return refs
        if lead.client_id:
            refs.setdefault(CLIENT, set()).add(lead.client_id)
        quote_ids = set(
            (
                await self.db.execute(
                    select(Quote.id).where(Quote.lead_id == subject_id)
                )
            )
            .scalars()
            .all()
        )
        if quote_ids:
            refs.setdefault(QUOTE, set()).update(quote_ids)
            booking_ids = set(
                (
                    await self.db.execute(
                        select(Booking.id).where(Booking.quote_id.in_(quote_ids))
                    )
                )
                .scalars()
                .all()
            )
            if booking_ids:
                refs.setdefault(BOOKING, set()).update(booking_ids)
        return refs

    async def _lead_behind(self, kind: str, subject_id: uuid.UUID) -> Lead | None:
        """The lead an entry's stamps belong to, whatever it was logged against.

        A call about a quote is contact with that quote's lead: the stamps are
        what the morning list reads, and an agent who logs against the quote
        rather than the lead has not failed to make the call.
        """
        if kind == LEAD:
            return await self.db.get(Lead, subject_id)
        if kind == QUOTE:
            quote = await self.db.get(Quote, subject_id)
            if quote is not None and quote.lead_id:
                return await self.db.get(Lead, quote.lead_id)
            return None
        if kind == BOOKING:
            booking = await self.db.get(Booking, subject_id)
            if booking is None:
                return None
            quote = await self.db.get(Quote, booking.quote_id)
            if quote is not None and quote.lead_id:
                return await self.db.get(Lead, quote.lead_id)
            return None
        # A client is deliberately not walked back to a lead. One client has
        # many, and stamping an arbitrary one would put a conversation about
        # this year's trip against last year's enquiry.
        return None

    async def _restamp(self, row: Communication) -> None:
        lead = await self._lead_behind(row.subject, row.subject_id)
        if lead is not None:
            await self.recompute(lead.id)


def _later(current: datetime | None, when: datetime) -> datetime:
    return when if current is None or when > current else current


def as_contact(row: Communication) -> Contact:
    """The ORM row as the pure rules see it."""
    return Contact(
        channel=row.channel,
        direction=row.direction,
        occurred_at=row.occurred_at,
        reached=row.reached,
        voided=row.is_voided,
    )


__all__ = ["CommsService", "as_contact", "normalise_subject"]
