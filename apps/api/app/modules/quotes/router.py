from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.quotes.assembly import QuoteAssemblyService
from app.modules.quotes.models import QuoteVersion
from app.modules.quotes.option_pricing import (
    OptionCosting,
    OptionPricingResult,
    OptionPricingService,
)
from app.modules.quotes.pricing_service import QuotePricingService
from app.modules.quotes.schemas import (
    CalculateRequest,
    CohortPriceRead,
    OptionBuildUpInternal,
    OptionPricingClientResult,
    OptionPricingInternalResult,
    PricedLegInternalRead,
    PricedLegRead,
    PricingLineClient,
    PricingResultClient,
    PricingResultInternal,
    QuoteCreate,
    QuoteOptionClientRead,
    QuoteOptionIn,
    QuoteOptionInternalRead,
    QuoteOptionResolvedRead,
    QuoteOptionUpdate,
    QuoteRead,
    QuoteStatusUpdate,
    QuoteSummary,
    QuoteUpdate,
    QuoteVersionClientRead,
    QuoteVersionInternalRead,
    QuoteVersionSummary,
    ReadinessRead,
    RejectedCandidateFullRead,
    RejectedCandidateIn,
    RejectedCandidateRead,
    SelectOptionIn,
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


@router.patch("/quotes/{quote_id}", response_model=QuoteRead)
async def update_quote(
    quote_id: uuid.UUID,
    body: QuoteUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Edit the quote's own fields — currently the document's cover copy (§3.11)."""
    return await QuoteService(db).update_quote(
        quote_id, body.model_dump(exclude_unset=True)
    )


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
        cohorts=[
            CohortPriceRead(
                residence=price.cohort.residence,
                traveller_type=price.cohort.traveller_type,
                headcount=price.cohort.count,
                currency=price.currency,
                per_person=price.per_person,
                total=price.total,
            )
            for price in (
                costing.cohort_prices.cohorts if costing.cohort_prices else ()
            )
        ],
        legs=[
            PricedLegRead(
                sequence=one.sequence,
                accommodation_name=one.accommodation_name,
                room_type_name=one.room.room_type_name,
                meal_plan_code=one.plan_code,
                rooms_required=one.room.rooms,
                nights=one.nights,
            )
            for one in costing.legs
        ],
        conversions=(
            dict(costing.cohort_prices.conversions) if costing.cohort_prices else {}
        ),
    )


def _internal_option(costing: OptionCosting) -> QuoteOptionInternalRead:
    return QuoteOptionInternalRead(
        # `legs` is replaced below with the internal rows, which carry the
        # fallback reason the client schema deliberately omits.
        **_client_option(costing).model_dump(exclude={"legs"}),
        room_type_id=costing.room_type_id,
        meal_plan_id=costing.meal_plan_id,
        meal_plan_name=costing.meal_plan_name,
        meal_plan_fallback_from=costing.meal_plan_fallback_from,
        supplier_paid_total=costing.supplier_paid_total,
        retained_discount=costing.retained_discount,
        supplements=[
            SupplementChargeInternal.model_validate(s) for s in costing.supplements
        ],
        legs=[
            PricedLegInternalRead(
                sequence=one.sequence,
                accommodation_name=one.accommodation_name,
                room_type_name=one.room.room_type_name,
                meal_plan_code=one.plan_code,
                rooms_required=one.room.rooms,
                nights=one.nights,
                meal_plan_fallback_from=(
                    one.requested_plan if one.is_fallback else None
                ),
            )
            for one in costing.legs
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


# --------------------------------------------------------------------------- #
# Stage 3.4 assembly: options, refusals, readiness, issuing
# --------------------------------------------------------------------------- #


@router.post(
    "/quotes/{quote_id}/options",
    response_model=QuoteOptionResolvedRead,
    status_code=201,
)
async def add_quote_option(
    quote_id: uuid.UUID,
    body: QuoteOptionIn,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Offer another property on the quote. Room type and plan resolve at pricing."""
    return await QuoteAssemblyService(db).add_option(quote_id, body)


@router.patch(
    "/quotes/{quote_id}/options/{option_id}", response_model=QuoteOptionResolvedRead
)
async def update_quote_option(
    quote_id: uuid.UUID,
    option_id: uuid.UUID,
    body: QuoteOptionUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Edit one option. `is_recommended: true` moves the recommendation here."""
    return await QuoteAssemblyService(db).update_option(quote_id, option_id, body)


@router.delete("/quotes/{quote_id}/options/{option_id}", status_code=204)
async def remove_quote_option(
    quote_id: uuid.UUID,
    option_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    await QuoteAssemblyService(db).remove_option(quote_id, option_id)


@router.post(
    "/quotes/{quote_id}/rejected-candidates",
    response_model=RejectedCandidateFullRead,
    status_code=201,
)
async def add_rejected_candidate(
    quote_id: uuid.UUID,
    body: RejectedCandidateIn,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Record a property considered and ruled out by hand (§3.3a).

    The reason prints on the quotation verbatim, so it must contain only what is
    safe to show a client — never a cost, margin or supplier-relations reason.
    """
    return await QuoteAssemblyService(db).add_rejected_candidate(quote_id, body)


@router.delete(
    "/quotes/{quote_id}/rejected-candidates/{candidate_id}", status_code=204
)
async def remove_rejected_candidate(
    quote_id: uuid.UUID,
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Remove an agent-typed refusal. Engine-derived ones are not removable."""
    await QuoteAssemblyService(db).remove_rejected_candidate(quote_id, candidate_id)


@router.get("/quotes/{quote_id}/readiness", response_model=ReadinessRead)
async def quote_readiness(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:read")),
):
    """Whether the quote can be issued, and everything wrong with it either way.

    Blocking problems would put a wrong figure in front of a client; advisory
    ones only make for a weaker proposal. Writes nothing.
    """
    return await QuoteAssemblyService(db).readiness(quote_id)


@router.post("/quotes/{quote_id}/issue", response_model=None)
async def issue_quote(
    quote_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("quote:issue")),
) -> QuoteVersionInternalRead | QuoteVersionClientRead:
    """Freeze the quote into an immutable version and mark it sent (§3.11).

    Prices the options first, then refuses on any blocking readiness problem —
    reporting all of them at once. Re-issuing appends another version; nothing
    already issued is ever rewritten.
    """
    version = await QuoteAssemblyService(db).issue(quote_id, actor_id=actor.id)
    if _can_read_cost(actor):
        return QuoteVersionInternalRead.model_validate(version)
    return QuoteVersionClientRead.model_validate(version)


@router.post("/quotes/{quote_id}/select", response_model=QuoteRead)
async def select_quote_option(
    quote_id: uuid.UUID,
    body: SelectOptionIn,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission("quote:create")),
):
    """Record which option the client chose (§7).

    Does not change the quote's status: choosing an option and accepting a
    quotation are separate events, and the gap between them is worth keeping.
    """
    return await QuoteAssemblyService(db).select_option(quote_id, body.option_id)
