"""Writing, reviewing and reading proposal copy (§4.4).

The whole value of this module is one rule: **nothing reaches a client document
until a person approves it.** Everything else here exists to make that rule
hold when the pipeline gets busy — a provider that is unavailable, an agent who
rewrites a draft, a second description approved over the first.

The brief is assembled here rather than by the caller, from the catalogue.
That is deliberate: a provider given free text will write about whatever it is
handed, and a brief built from the property's own rate rows can only describe
board bases we can actually sell. The one free-text input is the agent's steer,
because an agent who has visited the property knows the thing worth saying.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.integrations.narrative import (
    ACCOMMODATION,
    DESTINATION,
    HAND,
    Brief,
    Draft,
    NarrativeProvider,
    NarrativeUnavailable,
    UnavailableProvider,
)
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.destinations.models import Destination
from app.modules.narratives.models import (
    APPROVED,
    DRAFT,
    REJECTED,
    STATUSES,
    Narrative,
)


class NarrativeService:
    def __init__(
        self, db: AsyncSession, *, provider: NarrativeProvider | None = None
    ):
        self.db = db
        # Injected so a test — and later a real vendor — plugs in without this
        # service changing. The default is the one that refuses, because no
        # model is configured for this project and filler is worse than nothing
        # (see the seam's module docstring).
        self.provider: NarrativeProvider = provider or UnavailableProvider()

    # -- reading -------------------------------------------------------------- #

    async def printable(
        self, subject: str, subject_id: uuid.UUID
    ) -> Narrative | None:
        """The copy a client document may use, or ``None``.

        Approved, not superseded, newest first. A draft is never returned, and
        that is the only question the document layer is allowed to ask.
        """
        return (
            (
                await self.db.execute(
                    select(Narrative)
                    .where(
                        Narrative.subject == subject,
                        Narrative.subject_id == subject_id,
                        Narrative.status == APPROVED,
                        Narrative.superseded_at.is_(None),
                    )
                    .order_by(Narrative.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def history(
        self, subject: str, subject_id: uuid.UUID
    ) -> list[Narrative]:
        """Everything ever written about this subject, newest first."""
        return list(
            (
                await self.db.execute(
                    select(Narrative)
                    .where(
                        Narrative.subject == subject,
                        Narrative.subject_id == subject_id,
                    )
                    .order_by(Narrative.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def awaiting(self, status: str = DRAFT) -> list[Narrative]:
        """The review queue, newest first.

        A gate nobody can see the queue for is a gate that quietly becomes a
        rubber stamp, so "what is waiting for review" is a first-class question
        rather than something to be filtered out of a per-property listing.
        """
        if status not in STATUSES:
            raise AppError(f"status must be one of {', '.join(STATUSES)}.")
        return list(
            (
                await self.db.execute(
                    select(Narrative)
                    .where(Narrative.status == status)
                    .order_by(Narrative.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )

    # -- writing -------------------------------------------------------------- #

    async def brief_for(
        self, subject: str, subject_id: uuid.UUID, *, steer: str = "", words: int = 80
    ) -> Brief:
        """The facts about one subject, as a brief.

        Board bases come from the **rates on file**, not from a property's
        marketing: a narrative promising half board we cannot sell is a
        narrative that costs a booking when the client asks for it.
        """
        if subject == ACCOMMODATION:
            property_row = await self.db.get(Accommodation, subject_id)
            if property_row is None:
                raise NotFoundError("Accommodation not found.")
            place = (
                await self.db.get(Destination, property_row.destination_id)
                if property_row.destination_id
                else None
            )
            plans = (
                (
                    await self.db.execute(
                        select(MealPlan.name)
                        .join(
                            AccommodationRate,
                            AccommodationRate.meal_plan_id == MealPlan.id,
                        )
                        .where(
                            AccommodationRate.accommodation_id == subject_id,
                            AccommodationRate.is_active.is_(True),
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            rooms = (
                (
                    await self.db.execute(
                        select(RoomType.name).where(
                            RoomType.accommodation_id == subject_id,
                            RoomType.is_active.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return Brief(
                subject=ACCOMMODATION,
                name=property_row.name,
                category=property_row.category or "",
                place=place.name if place else "",
                meal_plans=tuple(sorted(plans)),
                room_types=tuple(sorted(rooms)),
                steer=steer,
                words=words,
            )

        if subject == DESTINATION:
            place = await self.db.get(Destination, subject_id)
            if place is None:
                raise NotFoundError("Destination not found.")
            return Brief(
                subject=DESTINATION,
                name=place.name,
                category=place.type or "",
                place=place.region or place.country or "",
                steer=steer,
                words=words,
            )

        raise AppError(
            f"'{subject}' is not something a narrative can be about. Use "
            f"'{ACCOMMODATION}' or '{DESTINATION}'."
        )

    async def generate(
        self,
        subject: str,
        subject_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
        steer: str = "",
        words: int = 80,
    ) -> Narrative:
        """Ask the provider for a draft, and store it as a draft.

        A provider that cannot answer raises, and the raise is passed on with
        its own words rather than turned into an empty description: an agent
        told "no provider is configured" writes the paragraph, and an agent
        handed a blank one does not notice.
        """
        brief = await self.brief_for(subject, subject_id, steer=steer, words=words)
        try:
            draft = await self.provider.write(brief)
        except NarrativeUnavailable as exc:
            raise AppError(str(exc)) from exc
        return await self._store(brief, draft, actor_id=actor_id, subject_id=subject_id)

    async def compose(
        self,
        subject: str,
        subject_id: uuid.UUID,
        *,
        text: str,
        actor_id: uuid.UUID | None,
    ) -> Narrative:
        """Store an agent's own writing, as a draft on the same path.

        The identical review gate on purpose. An agent's paragraph is usually
        the better one — they have stayed at the property — but "who wrote it"
        is not what the gate is for: it is for making sure somebody other than
        the author looked at it before a client did.
        """
        brief = await self.brief_for(subject, subject_id)
        return await self._store(
            brief,
            Draft(text=text, provider=HAND),
            actor_id=actor_id,
            subject_id=subject_id,
        )

    async def revise(
        self, narrative_id: uuid.UUID, *, text: str, actor_id: uuid.UUID | None
    ) -> Narrative:
        """Edit a draft before it is reviewed.

        Only a draft. Editing an approved narrative would put words in front of
        a client that nobody approved — the approval is of a *text*, not of a
        row — so a change to approved copy is a new draft and a new review.
        """
        row = await self._get(narrative_id)
        if row.status != DRAFT:
            raise AppError(
                f"This narrative is {row.status}, not a draft. Approval is of a "
                f"text, not of a row, so editing it would put unapproved words "
                f"in front of a client — write a new draft instead."
            )
        row.text = text
        # The editor becomes the author: they are the one answerable for the
        # sentence now, and a model that produced the first version did not
        # write this one.
        row.created_by = actor_id or row.created_by
        if row.provider != HAND:
            row.provider = f"{row.provider}+{HAND}"
        await self.db.commit()
        await self.db.refresh(row)
        return row

    # -- reviewing ------------------------------------------------------------ #

    async def approve(
        self, narrative_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> Narrative:
        """Let this text reach a client, and supersede whatever it replaces.

        The previous approved narrative is **kept** and marked superseded, not
        deleted: an issued proposal said what it said, and the row is the only
        record of it.
        """
        row = await self._get(narrative_id)
        if row.status == APPROVED:
            return row
        if row.status == REJECTED:
            raise AppError(
                "This narrative was rejected. Write a new draft rather than "
                "reviving one somebody has already turned down."
            )
        now = datetime.now(UTC)
        standing = await self.printable(row.subject, row.subject_id)
        if standing is not None and standing.id != row.id:
            standing.superseded_at = now
        row.status = APPROVED
        row.reviewed_by = actor_id
        row.reviewed_at = now
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def reject(
        self,
        narrative_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
        note: str | None = None,
    ) -> Narrative:
        """Turn a draft down, with a reason for whoever writes the next one."""
        row = await self._get(narrative_id)
        if row.status == APPROVED:
            raise AppError(
                "This narrative is approved and may already be on an issued "
                "proposal. Approve a replacement instead — that supersedes it "
                "and keeps the record of what was sent."
            )
        row.status = REJECTED
        row.reviewed_by = actor_id
        row.reviewed_at = datetime.now(UTC)
        row.review_note = note
        await self.db.commit()
        await self.db.refresh(row)
        return row

    # -- helpers -------------------------------------------------------------- #

    async def _store(
        self,
        brief: Brief,
        draft: Draft,
        *,
        actor_id: uuid.UUID | None,
        subject_id: uuid.UUID,
    ) -> Narrative:
        row = Narrative(
            subject=brief.subject,
            subject_id=subject_id,
            status=DRAFT,
            text=draft.text.strip(),
            provider=draft.provider,
            model=draft.model or None,
            brief=brief.as_dict(),
            created_by=actor_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def _get(self, narrative_id: uuid.UUID) -> Narrative:
        row = await self.db.get(Narrative, narrative_id)
        if row is None:
            raise NotFoundError("Narrative not found.")
        return row
