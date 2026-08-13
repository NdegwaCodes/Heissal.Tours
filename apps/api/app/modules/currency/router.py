from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.currency.models import Currency, ExchangeRate
from app.modules.currency.schemas import (
    CurrencyCreate,
    CurrencyRead,
    CurrencyUpdate,
    ExchangeRateCreate,
    ExchangeRateRead,
)
from app.modules.users.models import User

router = APIRouter(tags=["reference"])


# --- Currencies ---

@router.get("/currencies", response_model=list[CurrencyRead])
async def list_currencies(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("currency:read")),
):
    return await CRUDService(db, Currency, pk="code").list()


@router.post("/currencies", response_model=CurrencyRead, status_code=201)
async def create_currency(
    body: CurrencyCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("currency:manage")),
):
    data = body.model_dump()
    data["code"] = data["code"].upper()
    return await CRUDService(db, Currency, pk="code").create(data)


@router.patch("/currencies/{code}", response_model=CurrencyRead)
async def update_currency(
    code: str,
    body: CurrencyUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("currency:manage")),
):
    return await CRUDService(db, Currency, pk="code").update(
        code.upper(), body.model_dump(exclude_unset=True)
    )


# --- Exchange rates ---

@router.get("/exchange-rates", response_model=list[ExchangeRateRead])
async def list_exchange_rates(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("fx:read")),
):
    rows = (
        await db.execute(select(ExchangeRate).order_by(ExchangeRate.effective_from.desc()))
    ).scalars().all()
    return rows


@router.post("/exchange-rates", response_model=ExchangeRateRead, status_code=201)
async def create_exchange_rate(
    body: ExchangeRateCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("fx:manage")),
):
    rate = ExchangeRate(
        base_currency=body.base_currency.upper(),
        quote_currency=body.quote_currency.upper(),
        rate=body.rate,
        effective_from=body.effective_from,
        source=body.source,
        created_by=actor.id,
    )
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return rate
