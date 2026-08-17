"""Quote pricing service (Stage 2.8) — the seam between the API and the engine.

Two entry points share one engine:
- ``calculate`` prices a transient request and returns the breakdown WITHOUT
  persisting (the live quote builder).
- ``price_quote`` prices a saved quote and appends an immutable ``quote_version``
  (with its ``quote_items``), then points ``quotes.current_version_id`` at it.
  Re-pricing never mutates an existing version — it always creates the next one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.quotes.engine import (
    AccommodationInput,
    ActivityInput,
    LegInput,
    PricingEngine,
    PricingInputs,
    TransportInput,
    TravellerInput,
)
from app.modules.quotes.models import Quote, QuoteItem, QuoteVersion
from app.modules.quotes.schemas import (
    CalculateRequest,
    PricingResultInternal,
)


def inputs_from_request(req: CalculateRequest) -> PricingInputs:
    return PricingInputs(
        residence_category_id=req.residence_category_id,
        presentation_currency=req.presentation_currency,
        arrival_date=req.arrival_date,
        departure_date=req.departure_date,
        markup_pct=req.markup_pct,
        discount_pct=req.discount_pct,
        tax_pct=req.tax_pct,
        travellers=[TravellerInput(t.traveller_type, t.age) for t in req.travellers],
        legs=[
            LegInput(
                destination_id=leg.destination_id,
                nights=leg.nights,
                check_in=leg.check_in,
                accommodations=[
                    AccommodationInput(
                        a.accommodation_id, a.room_type_id, a.meal_plan_id, a.rooms, a.nights
                    )
                    for a in leg.accommodations
                ],
                activities=[
                    ActivityInput(ac.activity_id, ac.adults, ac.children)
                    for ac in leg.activities
                ],
            )
            for leg in req.legs
        ],
        transport=[
            TransportInput(t.vehicle_id, t.estimated_km, t.days) for t in req.transport
        ],
    )


def inputs_from_quote(quote: Quote) -> PricingInputs:
    return PricingInputs(
        residence_category_id=quote.residence_category_id,
        presentation_currency=quote.presentation_currency,
        arrival_date=quote.arrival_date,
        departure_date=quote.departure_date,
        markup_pct=quote.markup_pct,
        discount_pct=quote.discount_pct,
        tax_pct=quote.tax_pct,
        travellers=[TravellerInput(t.traveller_type, t.age) for t in quote.travellers],
        legs=[
            LegInput(
                destination_id=leg.destination_id,
                nights=leg.nights,
                check_in=leg.check_in,
                accommodations=[
                    AccommodationInput(
                        a.accommodation_id, a.room_type_id, a.meal_plan_id, a.rooms, a.nights
                    )
                    for a in leg.accommodations
                ],
                activities=[
                    ActivityInput(ac.activity_id, ac.adults, ac.children)
                    for ac in leg.activities
                ],
            )
            for leg in quote.legs
        ],
        transport=[
            TransportInput(t.vehicle_id, t.estimated_km, t.days) for t in quote.transport
        ],
    )


class QuotePricingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.engine = PricingEngine(db)

    async def calculate(self, req: CalculateRequest) -> PricingResultInternal:
        result = await self.engine.compute(inputs_from_request(req))
        return PricingResultInternal.model_validate(result)

    async def _load_quote(self, quote_id: uuid.UUID) -> Quote:
        quote = (
            await self.db.execute(select(Quote).where(Quote.id == quote_id))
        ).scalar_one_or_none()
        if quote is None:
            raise NotFoundError("Quote not found.")
        return quote

    async def price_quote(
        self, quote_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> QuoteVersion:
        quote = await self._load_quote(quote_id)
        result = await self.engine.compute(inputs_from_quote(quote))
        internal = PricingResultInternal.model_validate(result)

        next_number = (
            await self.db.execute(
                select(func.coalesce(func.max(QuoteVersion.version_number), 0) + 1).where(
                    QuoteVersion.quote_id == quote_id
                )
            )
        ).scalar_one()

        version = QuoteVersion(
            quote_id=quote.id,
            version_number=int(next_number),
            snapshot=internal.model_dump(mode="json"),
            internal_cost=internal.internal_cost,
            selling_price=internal.selling_price,
            gross_profit=internal.gross_profit,
            gross_margin=internal.gross_margin,
            currency=internal.presentation_currency,
            created_by=actor_id,
        )
        for line in internal.lines:
            version.items.append(
                QuoteItem(
                    category=line.category,
                    description=line.description,
                    quantity=line.quantity,
                    source_currency=line.source_currency,
                    internal_cost=line.internal_cost,
                    unit_price=line.client_price,
                )
            )
        self.db.add(version)
        await self.db.flush()

        quote.current_version_id = version.id
        await self.db.commit()

        return (
            await self.db.execute(select(QuoteVersion).where(QuoteVersion.id == version.id))
        ).scalar_one()
