from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.quotes.schemas import (
    QuoteCreate,
    QuoteRead,
    QuoteStatusUpdate,
    QuoteSummary,
)
from app.modules.quotes.service import QuoteService
from app.modules.users.models import User

router = APIRouter(tags=["quotes"])


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
