from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.quotes.models import QuoteVersion
from app.modules.quotes.option_pricing import (
    OptionCosting,
    OptionPricingResult,
    OptionPricingService,
)
from app.modules.quotes.pricing_service import QuotePricingService
from app.modules.quotes.schemas import (
    CalculateRequest,
    OptionBuildUpInternal,
    OptionPricingClientResult,
    OptionPricingInternalResult,
    PricingLineClient,
    PricingResultClient,
    PricingResultInternal,
    QuoteCreate,
    QuoteOptionClientRead,
    QuoteOptionInternalRead,
    QuoteRead,
    QuoteStatusUpdate,
    QuoteSummary,
    QuoteVersionClientRead,
    QuoteVersionInternalRead,
    QuoteVersionSummary,
    RejectedCandidateRead,
    SupplementChargeInternal,
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


# --------------------------------------------------------------------------- #
# Stage 3 option pricing
# --------------------------------------------------------------------------- #


def _client_option(costing: OptionCosting) -> QuoteOptionClientRead:
    return QuoteOptionClientRead(
        accommodation_id=costing.accommodation_id,
        accommodation_name=costing.accommodation_name,
        room_type_name=costing.room_type_name,
        meal_plan_code=costing.meal_plan_code,
        rooms_required=costing.rooms_required,
        nights=costing.nights,
        currency=costing.currency,
        per_person=costing.build_up.per_person,
        group_total=costing.build_up.group_total,
        is_comparable=costing.is_comparable,
    )


def _internal_option(costing: OptionCosting) -> QuoteOptionInternalRead:
    return QuoteOptionInternalRead(
        **_client_option(costing).model_dump(),
        room_type_id=costing.room_type_id,
        meal_plan_id=costing.meal_plan_id,
        meal_plan_fallback_from=costing.meal_plan_fallback_from,
        supplier_paid_total=costing.supplier_paid_total,
        retained_discount=costing.retained_discount,
        supplements=[
            SupplementChargeInternal.model_validate(s) for s in costing.supplements
        ],
        build_up=OptionBuildUpInternal.model_validate(costing.build_up),
        warnings=costing.warnings,
    )


def _option_result(
    result: OptionPricingResult, *, internal: bool
) -> OptionPricingInternalResult | OptionPricingClientResult:
    rejected = [RejectedCandidateRead.model_validate(r) for r in result.rejected]
    if internal:
        return OptionPricingInternalResult(
            options=[_internal_option(o) for o in result.options],
            rejected=rejected,
            warnings=result.warnings,
        )
    return OptionPricingClientResult(
        options=[_client_option(o) for o in result.options], rejected=rejected
    )


@router.post("/quotes/{quote_id}/options/price", response_model=None)
async def price_quote_options(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("quote:create")),
) -> OptionPricingInternalResult | OptionPricingClientResult:
    """Price every option on a quote (§3.3-§3.7).

    Resolves each property's cheapest eligible room type, the meal plan after the
    fallback chain, the rooming, the mandatory supplements and the margin
    build-up, and records any property refused on minimum stay as a missed-out
    option. Cost, margin and supplier figures are returned only to staff holding
    ``quote:read_cost``.
    """
    result = await OptionPricingService(db).price_options(quote_id)
    return _option_result(result, internal=_can_read_cost(actor))
