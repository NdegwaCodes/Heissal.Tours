from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.quotes.models import QuoteVersion
from app.modules.quotes.pricing_service import QuotePricingService
from app.modules.quotes.schemas import (
    CalculateRequest,
    PricingLineClient,
    PricingResultClient,
    PricingResultInternal,
    QuoteCreate,
    QuoteRead,
    QuoteStatusUpdate,
    QuoteSummary,
    QuoteVersionClientRead,
    QuoteVersionInternalRead,
    QuoteVersionSummary,
)
from app.modules.quotes.service import QuoteService
from app.modules.users.models import User

router = APIRouter(tags=["quotes"])


def _can_read_cost(user: User) -> bool:
    keys = user.permission_keys
    return "*" in keys or "quote:read_cost" in keys


def _to_client_result(internal: PricingResultInternal) -> PricingResultClient:
    return PricingResultClient(
        presentation_currency=internal.presentation_currency,
        selling_price=internal.selling_price,
        lines=[
            PricingLineClient(
                category=ln.category,
                description=ln.description,
                quantity=ln.quantity,
                client_price=ln.client_price,
            )
            for ln in internal.lines
        ],
    )


@router.get("/quotes", response_model=list[QuoteSummary])
async def list_quotes(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    return await QuoteService(db).list_quotes()


@router.post("/quotes", response_model=QuoteRead, status_code=201)
async def create_quote(
    body: QuoteCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("quote:create")),
):
    return await QuoteService(db).create_quote(body, actor_id=actor.id)


@router.get("/quotes/{quote_id}", response_model=QuoteRead)
async def get_quote(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    return await QuoteService(db).get_quote(quote_id)


@router.patch("/quotes/{quote_id}/status", response_model=QuoteRead)
async def set_quote_status(
    quote_id: uuid.UUID,
    body: QuoteStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    return await QuoteService(db).set_status(quote_id, body.status)


@router.post("/quotes/calculate", response_model=None)
async def calculate_quote(
    body: CalculateRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("quote:create")),
) -> PricingResultInternal | PricingResultClient:
    """Price a transient quote without saving it (live builder).

    Cost and margin are included only for staff with ``quote:read_cost``.
    """
    result = await QuotePricingService(db).calculate(body)
    return result if _can_read_cost(actor) else _to_client_result(result)


@router.post("/quotes/{quote_id}/price", response_model=None)
async def price_quote(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("quote:create")),
) -> QuoteVersionInternalRead | QuoteVersionClientRead:
    """Compute and persist a new immutable version for a saved quote."""
    version = await QuotePricingService(db).price_quote(quote_id, actor_id=actor.id)
    if _can_read_cost(actor):
        return QuoteVersionInternalRead.model_validate(version)
    return QuoteVersionClientRead.model_validate(version)


@router.get("/quotes/{quote_id}/versions", response_model=list[QuoteVersionSummary])
async def list_quote_versions(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    stmt = (
        select(QuoteVersion)
        .where(QuoteVersion.quote_id == quote_id)
        .order_by(QuoteVersion.version_number.desc())
    )
    return list((await db.execute(stmt)).scalars().all())
