"""Leads and the pipeline over the API (§5.2)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.leads.models import Lead, LeadStage
from app.modules.leads.schemas import (
    AttentionRead,
    LeadAttentionRead,
    LeadCreate,
    LeadMove,
    LeadRead,
    LeadStageRead,
    LeadStageUpdate,
    LeadUpdate,
    PipelineRead,
    SourceCountRead,
    StageCountRead,
)
from app.modules.leads.service import LeadService
from app.modules.users.models import User

router = APIRouter(tags=["leads"])

READ = "lead:read"
MANAGE = "lead:manage"
PIPELINE = "lead:configure_pipeline"


@router.get("/lead-stages", response_model=list[LeadStageRead])
async def list_stages(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """The configured pipeline, in order. Seeds a generic one on first use."""
    return await LeadService(db).stages()


@router.patch("/lead-stages/{stage_id}", response_model=LeadStageRead)
async def rename_stage(
    stage_id: uuid.UUID,
    body: LeadStageUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(PIPELINE)),
):
    """Rename or reorder a stage.

    Safe by construction: every report asks which stage *means* won rather than
    comparing against a name, so "Won" can become "Booked and deposit paid"
    without a figure changing.
    """
    return await LeadService(db).rename_stage(
        stage_id,
        name=body.name,
        sort_order=body.sort_order,
        description=body.description,
    )


@router.post("/leads", response_model=LeadRead, status_code=201)
async def create_lead(
    body: LeadCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Record an enquiry.

    The next action is defaulted a few days out where none is given — demanding
    one would make the form an obstacle while the phone is ringing, and leaving
    it empty is how a lead disappears.
    """
    return await LeadService(db).create(
        actor_id=actor.id, **body.model_dump(exclude_unset=False)
    )


@router.get("/leads", response_model=list[LeadRead])
async def list_leads(
    stage_id: uuid.UUID | None = Query(default=None),
    owner_id: uuid.UUID | None = Query(default=None),
    source: str | None = Query(default=None),
    open_only: bool = Query(
        default=False, description="Exclude leads at a won or lost stage."
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    stmt = select(Lead)
    if stage_id:
        stmt = stmt.where(Lead.stage_id == stage_id)
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    if source:
        from app.modules.leads.service import normalise_source

        stmt = stmt.where(Lead.source == normalise_source(source))
    if open_only:
        stmt = stmt.join(LeadStage, LeadStage.id == Lead.stage_id).where(
            LeadStage.is_won.is_(False), LeadStage.is_lost.is_(False)
        )
    stmt = stmt.order_by(Lead.created_at.desc()).limit(500)
    return (await db.execute(stmt)).scalars().all()


@router.get("/leads/attention", response_model=list[LeadAttentionRead])
async def leads_needing_attention(
    owner_id: uuid.UUID | None = Query(default=None),
    stale_after_days: int = Query(default=14, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """The morning list: every open lead that needs looking at, worst first.

    Leads with **no next action** come first, deliberately. One with nothing
    scheduled appears on no other list, annoys nobody, and dies quietly — it is
    the commonest way a CRM stops being used.
    """
    found = await LeadService(db).needs_attention(
        owner_id=owner_id, stale_after_days=stale_after_days
    )
    return [
        LeadAttentionRead(
            lead=LeadRead.model_validate(lead),
            reasons=[AttentionRead(**vars(one)) for one in reasons],
        )
        for lead, reasons in found
    ]


@router.get("/leads/pipeline", response_model=PipelineRead)
async def pipeline_summary(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """The pipeline by stage and by source (§5.2).

    Sources are answerable for **bookings**, not for enquiries: the win column
    comes from the quotes a lead produced and the outcome recorded on them
    (§5.1), which is the join that decides where marketing money goes.
    """
    service = LeadService(db)
    report = await service.summary()
    return PipelineRead(
        stages=[StageCountRead(**vars(one)) for one in report.stages],
        sources=[
            SourceCountRead(
                source=one.source,
                leads=one.leads,
                quoted=one.quoted,
                won=one.won,
                win_rate=one.win_rate,
            )
            for one in report.sources
        ],
        open_budget=report.open_budget,
        open_leads=report.open_leads,
        won_leads=report.won_leads,
        lost_leads=report.lost_leads,
        win_rate=report.win_rate,
        problems=await service.pipeline_problems(),
    )


@router.get("/leads/{lead_id}", response_model=LeadRead)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await LeadService(db).get(lead_id)


@router.patch("/leads/{lead_id}", response_model=LeadRead)
async def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    """Correct a lead's own fields. The stage moves through ``/move``."""
    return await LeadService(db).update(
        lead_id, body.model_dump(exclude_unset=True)
    )


@router.post("/leads/{lead_id}/move", response_model=LeadRead)
async def move_lead(
    lead_id: uuid.UUID,
    body: LeadMove,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Move a lead to another stage, and record the move.

    A stage change that does not write history is how a pipeline becomes a
    status column, so this is one call and not two. Moving to a stage that
    means **lost** needs a reason: "we lost eleven" is a fact nobody can act
    on, and "seven of them on price" is a decision.
    """
    return await LeadService(db).move(
        lead_id,
        stage_id=body.stage_id,
        actor_id=actor.id,
        note=body.note,
        lost_reason=body.lost_reason,
        next_action_on=body.next_action_on,
        next_action_note=body.next_action_note,
    )


@router.post("/leads/{lead_id}/next-action", response_model=LeadRead)
async def set_next_action(
    lead_id: uuid.UUID,
    on: date = Query(description="The day the next step is due."),
    note: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    """Schedule the next step, which is what keeps a lead alive.

    Its own endpoint because it is the thing an agent does most often — after a
    call, in one tap — and burying it in a general update makes it the field
    nobody fills in.
    """
    return await LeadService(db).update(
        lead_id, {"next_action_on": on, "next_action_note": note}
    )
