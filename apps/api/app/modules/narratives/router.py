"""Proposal copy over the API (§4.4).

Three permissions rather than two, and the third is the point:
``narrative:approve`` is what lets words reach a client. The same split as
issuing a quotation (§3.4) — composing something and putting it in front of a
client are different levels of trust — and it is the one thing here that would
be worth building even if no model is ever configured.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.integrations.narrative import ACCOMMODATION, DESTINATION
from app.modules.narratives.schemas import (
    NarrativeCompose,
    NarrativeGenerate,
    NarrativeRead,
    NarrativeReview,
    NarrativeRevise,
)
from app.modules.narratives.service import NarrativeService
from app.modules.users.models import User

router = APIRouter(tags=["narratives"])

READ = "narrative:read"
MANAGE = "narrative:manage"
APPROVE = "narrative:approve"


@router.get(
    "/accommodations/{accommodation_id}/narratives",
    response_model=list[NarrativeRead],
)
async def list_property_narratives(
    accommodation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """Everything ever written about this property, newest first.

    Including the rejected ones. A draft somebody turned down is the record of
    what was nearly sent, and the note on it is what stops the next writer
    making the same mistake.
    """
    return await NarrativeService(db).history(ACCOMMODATION, accommodation_id)


@router.get(
    "/destinations/{destination_id}/narratives", response_model=list[NarrativeRead]
)
async def list_destination_narratives(
    destination_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await NarrativeService(db).history(DESTINATION, destination_id)


@router.post(
    "/accommodations/{accommodation_id}/narratives/generate",
    response_model=NarrativeRead,
    status_code=201,
)
async def generate_property_narrative(
    accommodation_id: uuid.UUID,
    body: NarrativeGenerate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Ask the configured provider for a draft. Stored as a draft, always.

    With no provider configured this refuses and says so, rather than storing
    an empty description: an agent told "no provider is configured" writes the
    paragraph, and an agent handed a blank one does not notice.
    """
    return await NarrativeService(db).generate(
        ACCOMMODATION,
        accommodation_id,
        actor_id=actor.id,
        steer=body.steer,
        words=body.words,
    )


@router.post(
    "/destinations/{destination_id}/narratives/generate",
    response_model=NarrativeRead,
    status_code=201,
)
async def generate_destination_narrative(
    destination_id: uuid.UUID,
    body: NarrativeGenerate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    return await NarrativeService(db).generate(
        DESTINATION,
        destination_id,
        actor_id=actor.id,
        steer=body.steer,
        words=body.words,
    )


@router.post(
    "/accommodations/{accommodation_id}/narratives",
    response_model=NarrativeRead,
    status_code=201,
)
async def compose_property_narrative(
    accommodation_id: uuid.UUID,
    body: NarrativeCompose,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """An agent's own writing, onto the identical review path."""
    return await NarrativeService(db).compose(
        ACCOMMODATION, accommodation_id, text=body.text, actor_id=actor.id
    )


@router.post(
    "/destinations/{destination_id}/narratives",
    response_model=NarrativeRead,
    status_code=201,
)
async def compose_destination_narrative(
    destination_id: uuid.UUID,
    body: NarrativeCompose,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    return await NarrativeService(db).compose(
        DESTINATION, destination_id, text=body.text, actor_id=actor.id
    )


@router.patch("/narratives/{narrative_id}", response_model=NarrativeRead)
async def revise_narrative(
    narrative_id: uuid.UUID,
    body: NarrativeRevise,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(MANAGE)),
):
    """Edit a draft. An approved narrative is replaced rather than edited."""
    return await NarrativeService(db).revise(
        narrative_id, text=body.text, actor_id=actor.id
    )


@router.post("/narratives/{narrative_id}/approve", response_model=NarrativeRead)
async def approve_narrative(
    narrative_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(APPROVE)),
):
    """Let this text reach a client, superseding whatever it replaces."""
    return await NarrativeService(db).approve(narrative_id, actor_id=actor.id)


@router.post("/narratives/{narrative_id}/reject", response_model=NarrativeRead)
async def reject_narrative(
    narrative_id: uuid.UUID,
    body: NarrativeReview,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission(APPROVE)),
):
    """Turn a draft down, with a reason for whoever writes the next one."""
    return await NarrativeService(db).reject(
        narrative_id, actor_id=actor.id, note=body.note
    )


@router.get("/narratives", response_model=list[NarrativeRead])
async def list_narratives_awaiting_review(
    status: str = Query(default="draft", description="draft | approved | rejected"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    """The review queue: what is waiting, or what is standing.

    A gate nobody can see the queue for is a gate that quietly becomes a
    rubber stamp.
    """
    return await NarrativeService(db).awaiting(status)
