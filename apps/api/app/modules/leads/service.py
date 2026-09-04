"""Leads, their stages and the morning list (§5.2).

The rules are in :mod:`app.modules.leads.pipeline`; this is the half that talks
to the database. Two things it does that are worth reading before changing:

**A lead is never created without a next action.** If the caller does not give
one it is defaulted a few days out. That is not the system being pushy — it is
the single behaviour that decides whether a CRM survives contact with a busy
week, because a lead with nothing scheduled appears on no list and dies quietly.

**Every stage move writes an event.** The current stage is a convenience; the
history is the pipeline. Nothing here updates ``stage_id`` without appending to
``lead_stage_events``, which is why they are one method and not two.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.leads.models import COMMON_SOURCES, Lead, LeadStage, LeadStageEvent
from app.modules.leads.pipeline import (
    Attention,
    Counted,
    Pipeline,
    Stage,
    StageRefused,
    Watched,
    attention,
    check_move,
    check_pipeline,
    next_action_default,
    summarise,
)
from app.modules.quotes.models import Quote
from app.modules.quotes.outcomes import ACCEPTED

#: The pipeline seeded on first use. Generic on purpose: the client has not
#: told us their stages, and these are rows rather than code precisely so that
#: renaming them — or inserting "site inspection" — is an afternoon's admin and
#: not a migration.
DEFAULT_STAGES: tuple[tuple[str, str, int, bool, bool, bool], ...] = (
    ("new", "New enquiry", 10, True, False, False),
    ("qualified", "Qualified", 20, False, False, False),
    ("quoted", "Quoted", 30, False, False, False),
    ("negotiating", "Negotiating", 40, False, False, False),
    ("won", "Won", 50, False, True, False),
    ("lost", "Lost", 60, False, False, True),
)


def normalise_source(value: str | None) -> str:
    """A source as a report should group it.

    Trimmed and lower-cased with spaces folded to underscores, so "Walk in",
    "walk-in" and "walk_in" are one line in a table rather than three. Not
    validated against :data:`COMMON_SOURCES`: sources multiply with every
    campaign, and a lead refused because "instagram" is not in an enum is a
    lead somebody files under "other".
    """
    cleaned = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or "other"


class LeadService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- the pipeline's shape -------------------------------------------------- #

    async def stages(self) -> list[LeadStage]:
        """The configured stages, in order, seeding the defaults on first use.

        Seeded lazily rather than in the reference seeder because the pipeline
        is the client's to own: a fresh install gets something usable, and the
        first rename makes it theirs.
        """
        rows = list(
            (
                await self.db.execute(
                    select(LeadStage).order_by(LeadStage.sort_order)
                )
            )
            .scalars()
            .all()
        )
        if rows:
            return rows
        for key, name, order, default, won, lost in DEFAULT_STAGES:
            self.db.add(
                LeadStage(
                    key=key,
                    name=name,
                    sort_order=order,
                    is_default=default,
                    is_won=won,
                    is_lost=lost,
                )
            )
        await self.db.commit()
        return list(
            (
                await self.db.execute(
                    select(LeadStage).order_by(LeadStage.sort_order)
                )
            )
            .scalars()
            .all()
        )

    async def rename_stage(
        self,
        stage_id: uuid.UUID,
        *,
        name: str | None = None,
        sort_order: int | None = None,
        description: str | None = None,
    ) -> LeadStage:
        """Edit what an agent sees. The key, and the won/lost flags, stay.

        Renaming is safe by construction: every report asks "which stage means
        won" rather than comparing against a name, so "Won" can become "Booked
        and deposit paid" without a single figure changing.
        """
        row = await self.db.get(LeadStage, stage_id)
        if row is None:
            raise NotFoundError("Lead stage not found.")
        if name is not None:
            row.name = name
        if sort_order is not None:
            row.sort_order = sort_order
        if description is not None:
            row.description = description
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def pipeline_problems(self) -> list[str]:
        """What is wrong with the configured pipeline, in plain words."""
        return check_pipeline([_stage(row) for row in await self.stages()])

    # -- leads ---------------------------------------------------------------- #

    async def create(
        self,
        *,
        contact_name: str,
        source: str | None = None,
        source_detail: str | None = None,
        client_id: uuid.UUID | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
        owner_id: uuid.UUID | None = None,
        stage_id: uuid.UUID | None = None,
        destination_interest: str | None = None,
        travel_from: date | None = None,
        travel_to: date | None = None,
        pax_estimate: int | None = None,
        budget_amount=None,
        budget_currency: str | None = None,
        next_action_on: date | None = None,
        next_action_note: str | None = None,
        notes: str | None = None,
        actor_id: uuid.UUID | None = None,
        today: date | None = None,
    ) -> Lead:
        """Record an enquiry, at the stage new ones arrive at.

        The next action is defaulted rather than demanded. Demanding it would
        make the form an obstacle at the moment the phone is ringing; leaving
        it empty would let the lead disappear. A date a few days out is the
        version of this that survives a busy week, and it is visible on every
        list until somebody changes it.
        """
        stages = await self.stages()
        if stage_id is not None:
            landing = next((row for row in stages if row.id == stage_id), None)
            if landing is None:
                raise NotFoundError("Lead stage not found.")
        else:
            landing = next((row for row in stages if row.is_default), None)
            if landing is None:
                raise AppError(
                    "No stage is marked as the one new enquiries arrive at, so "
                    "there is nowhere to put this lead. Set one on the pipeline."
                )
        if travel_from and travel_to and travel_to < travel_from:
            raise AppError("travel_to cannot be before travel_from.")
        if (budget_amount is None) != (budget_currency is None):
            # Money is an amount AND a currency here as everywhere else, even
            # for a figure as soft as a stated budget.
            raise AppError(
                "A budget needs both an amount and a currency, or neither."
            )

        now = datetime.now(UTC)
        lead = Lead(
            source=normalise_source(source),
            source_detail=source_detail,
            client_id=client_id,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            stage_id=landing.id,
            stage_since=now,
            owner_id=owner_id,
            destination_interest=destination_interest,
            travel_from=travel_from,
            travel_to=travel_to,
            pax_estimate=pax_estimate,
            budget_amount=budget_amount,
            budget_currency=(budget_currency or "").upper() or None,
            next_action_on=next_action_on
            or next_action_default(today=today or date.today()),
            next_action_note=next_action_note,
            notes=notes,
        )
        self.db.add(lead)
        await self.db.flush()
        # The arrival is a stage event too: without it the history starts at
        # the first move and the time spent in the entry stage is invisible.
        self.db.add(
            LeadStageEvent(
                lead_id=lead.id,
                from_stage_id=None,
                to_stage_id=landing.id,
                at=now,
                by=actor_id,
                note="Enquiry received.",
            )
        )
        await self.db.commit()
        return await self.get(lead.id)

    async def get(self, lead_id: uuid.UUID) -> Lead:
        lead = (
            await self.db.execute(select(Lead).where(Lead.id == lead_id))
        ).scalar_one_or_none()
        if lead is None:
            raise NotFoundError("Lead not found.")
        return lead

    async def update(self, lead_id: uuid.UUID, data: dict) -> Lead:
        """Edit a lead's own fields. The stage moves through :meth:`move`.

        Two paths on purpose: everything here is a correction, and a stage
        change is an event with a time and an author. Folding them together
        would mean a typo fix could write a fictitious entry into the history
        the pipeline reports on.
        """
        lead = await self.get(lead_id)
        if "source" in data and data["source"] is not None:
            data["source"] = normalise_source(data["source"])
        if "budget_currency" in data and data["budget_currency"]:
            data["budget_currency"] = data["budget_currency"].upper()
        merged_from = data.get("travel_from", lead.travel_from)
        merged_to = data.get("travel_to", lead.travel_to)
        if merged_from and merged_to and merged_to < merged_from:
            raise AppError("travel_to cannot be before travel_from.")
        for field, value in data.items():
            setattr(lead, field, value)
        if (lead.budget_amount is None) != (lead.budget_currency is None):
            raise AppError(
                "A budget needs both an amount and a currency, or neither."
            )
        await self.db.commit()
        return await self.get(lead_id)

    async def move(
        self,
        lead_id: uuid.UUID,
        *,
        stage_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        note: str | None = None,
        lost_reason: str | None = None,
        next_action_on: date | None = None,
        next_action_note: str | None = None,
    ) -> Lead:
        """Move a lead, and record the move.

        One method, because a stage change that does not write history is how a
        pipeline becomes a status column — and everything worth reading (how
        long at each stage, where leads die) comes from the history.

        Moving to a **lost** stage requires a reason, which is the one thing
        the pure rules refuse. Moving to a terminal stage also clears the next
        action: a won deal has no follow-up call and a lost one is not a task,
        and leaving the date behind would keep both on somebody's morning list
        forever.
        """
        lead = await self.get(lead_id)
        target = await self.db.get(LeadStage, stage_id)
        if target is None:
            raise NotFoundError("Lead stage not found.")
        current = await self.db.get(LeadStage, lead.stage_id)
        try:
            check_move(
                _stage(current) if current else _stage(target),
                _stage(target),
                lost_reason=lost_reason or lead.lost_reason,
            )
        except StageRefused as exc:
            raise AppError(str(exc)) from exc

        now = datetime.now(UTC)
        self.db.add(
            LeadStageEvent(
                lead_id=lead.id,
                from_stage_id=lead.stage_id,
                to_stage_id=target.id,
                at=now,
                by=actor_id,
                note=note,
            )
        )
        lead.stage_id = target.id
        lead.stage_since = now
        if lost_reason:
            lead.lost_reason = lost_reason
        if target.is_won or target.is_lost:
            lead.next_action_on = None
            lead.next_action_note = None
        else:
            if next_action_on is not None:
                lead.next_action_on = next_action_on
            if next_action_note is not None:
                lead.next_action_note = next_action_note
        await self.db.commit()
        return await self.get(lead_id)

    # -- the morning list ----------------------------------------------------- #

    async def needs_attention(
        self,
        *,
        owner_id: uuid.UUID | None = None,
        stale_after_days: int = 14,
        today: date | None = None,
    ) -> list[tuple[Lead, list[Attention]]]:
        """Every open lead that needs looking at, and why.

        Sorted with the worst first: no next action, then most overdue. An
        unsorted list of forty leads is a list nobody works through.
        """
        when = today or date.today()
        stmt = select(Lead).join(LeadStage, LeadStage.id == Lead.stage_id).where(
            LeadStage.is_won.is_(False), LeadStage.is_lost.is_(False)
        )
        if owner_id is not None:
            stmt = stmt.where(Lead.owner_id == owner_id)
        leads = list((await self.db.execute(stmt)).scalars().all())

        out: list[tuple[Lead, list[Attention]]] = []
        for lead in leads:
            stage = await self.db.get(LeadStage, lead.stage_id)
            found = attention(
                Watched(
                    name=lead.contact_name,
                    stage=_stage(stage) if stage else _stage(None),
                    stage_since=lead.stage_since.date() if lead.stage_since else None,
                    next_action_on=lead.next_action_on,
                    owner=str(lead.owner_id) if lead.owner_id else None,
                ),
                today=when,
                stale_after_days=stale_after_days,
            )
            if found:
                out.append((lead, found))
        out.sort(key=lambda pair: (-max(one.days for one in pair[1]), pair[0].contact_name))
        out.sort(key=lambda pair: 0 if _has_no_next_action(pair[1]) else 1)
        return out

    async def summary(self, *, today: date | None = None) -> Pipeline:
        """The pipeline by stage and by source (§5.2).

        The quote counts come from ``quotes.lead_id`` and the wins from §5.1's
        outcome, which is the join that makes a source answerable for money
        rather than for activity.
        """
        stages = await self.stages()
        by_id = {row.id: row for row in stages}
        leads = list((await self.db.execute(select(Lead))).scalars().all())

        quoted: dict[uuid.UUID, int] = {
            lead_id: int(count)
            for lead_id, count in (
                await self.db.execute(
                    select(Quote.lead_id, func.count())
                    .where(Quote.lead_id.is_not(None))
                    .group_by(Quote.lead_id)
                )
            ).all()
            if lead_id is not None
        }
        accepted = set(
            (
                await self.db.execute(
                    select(Quote.lead_id).where(
                        Quote.lead_id.is_not(None), Quote.status == ACCEPTED
                    )
                )
            )
            .scalars()
            .all()
        )

        counted = [
            Counted(
                stage=_stage(by_id.get(lead.stage_id)),
                source=lead.source,
                stage_since=lead.stage_since.date() if lead.stage_since else None,
                budget_amount=lead.budget_amount,
                budget_currency=lead.budget_currency,
                quotes=int(quoted.get(lead.id, 0)),
                accepted=lead.id in accepted,
            )
            for lead in leads
        ]
        return summarise(
            counted, [_stage(row) for row in stages], today=today or date.today()
        )


def _has_no_next_action(found: list[Attention]) -> bool:
    from app.modules.leads.pipeline import NO_NEXT_ACTION

    return any(one.code == NO_NEXT_ACTION for one in found)


def _stage(row: LeadStage | None) -> Stage:
    """The ORM stage as the pure layer's, or a placeholder for a missing one."""
    if row is None:
        return Stage(key="unknown", name="Unknown")
    return Stage(
        key=row.key,
        name=row.name,
        sort_order=row.sort_order,
        is_default=row.is_default,
        is_won=row.is_won,
        is_lost=row.is_lost,
    )


__all__ = ["COMMON_SOURCES", "LeadService", "normalise_source", "DEFAULT_STAGES"]
