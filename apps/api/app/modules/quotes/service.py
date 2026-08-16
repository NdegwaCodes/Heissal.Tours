"""Quote assembly service (Stage 2.7).

Creates a quote and its full request tree (travellers, legs, per-leg
accommodation/activity selections, transport) in one transaction, allocates a
human-readable quote number from a per-year counter, and reads a quote back with
its nested selections. Pricing (versions, items, totals) is added in Stage 2.8;
nothing here computes money.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.clients.models import Client
from app.modules.quotes.models import (
    Quote,
    QuoteAccommodation,
    QuoteActivity,
    QuoteCounter,
    QuoteLeg,
    QuoteTransport,
    QuoteTraveller,
)
from app.modules.quotes.schemas import QuoteCreate
from app.modules.residence.models import ResidenceCategory


class QuoteNumberService:
    """Allocates ``HTQ-<year>-<NNNN>`` numbers from a locked per-year counter."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def next_number(self, year: int) -> str:
        counter = await self.db.get(QuoteCounter, year, with_for_update=True)
        if counter is None:
            self.db.add(QuoteCounter(year=year, last_value=0))
            await self.db.flush()
            counter = await self.db.get(QuoteCounter, year, with_for_update=True)
        assert counter is not None  # just inserted/locked
        counter.last_value += 1
        await self.db.flush()
        return f"HTQ-{year}-{counter.last_value:04d}"


class QuoteService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load(self, quote_id: uuid.UUID) -> Quote:
        # selectin relationships on the mapper load the full tree eagerly.
        stmt = select(Quote).where(Quote.id == quote_id)
        quote = (await self.db.execute(stmt)).scalar_one_or_none()
        if quote is None:
            raise NotFoundError("Quote not found.")
        return quote

    async def get_quote(self, quote_id: uuid.UUID) -> Quote:
        return await self._load(quote_id)

    async def list_quotes(self, *, limit: int = 200) -> list[Quote]:
        stmt = select(Quote).order_by(Quote.created_at.desc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    async def create_quote(self, payload: QuoteCreate, *, actor_id: uuid.UUID | None) -> Quote:
        client = await self.db.get(Client, payload.client_id)
        if client is None:
            raise NotFoundError("Client not found.")

        rc_id = payload.residence_category_id or client.residence_category_id
        if rc_id is None:
            raise AppError(
                "residence_category_id is required (the client has no default category)."
            )
        residence = await self.db.get(ResidenceCategory, rc_id)
        if residence is None:
            raise NotFoundError("Residence category not found.")

        currency = payload.presentation_currency or residence.default_currency_code
        if not currency:
            raise AppError(
                "presentation_currency is required (residence category has no default currency)."
            )
        currency = currency.upper()

        number = await QuoteNumberService(self.db).next_number(date.today().year)

        quote = Quote(
            quote_number=number,
            client_id=client.id,
            status="draft",
            presentation_currency=currency,
            residence_category_id=residence.id,
            arrival_date=payload.arrival_date,
            departure_date=payload.departure_date,
            markup_pct=payload.markup_pct,
            discount_pct=payload.discount_pct,
            tax_pct=payload.tax_pct,
            created_by=actor_id,
        )
        for t in payload.travellers:
            quote.travellers.append(
                QuoteTraveller(traveller_type=t.traveller_type, age=t.age)
            )
        for index, leg in enumerate(payload.legs, start=1):
            ql = QuoteLeg(
                sequence=index,
                destination_id=leg.destination_id,
                nights=leg.nights,
                check_in=leg.check_in,
                check_out=leg.check_out,
            )
            for acc in leg.accommodations:
                ql.accommodations.append(
                    QuoteAccommodation(
                        accommodation_id=acc.accommodation_id,
                        room_type_id=acc.room_type_id,
                        meal_plan_id=acc.meal_plan_id,
                        rooms=acc.rooms,
                        nights=acc.nights,
                    )
                )
            for act in leg.activities:
                ql.activities.append(
                    QuoteActivity(
                        activity_id=act.activity_id,
                        day=act.day,
                        adults=act.adults,
                        children=act.children,
                    )
                )
            quote.legs.append(ql)
        for tr in payload.transport:
            quote.transport.append(
                QuoteTransport(
                    vehicle_id=tr.vehicle_id,
                    estimated_km=tr.estimated_km,
                    days=tr.days,
                )
            )

        self.db.add(quote)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            # A bad destination/accommodation/vehicle reference lands here.
            raise AppError(
                "A referenced record (destination, accommodation, vehicle, …) "
                "does not exist or violates a constraint."
            ) from exc

        return await self._load(quote.id)

    async def set_status(self, quote_id: uuid.UUID, status: str) -> Quote:
        quote = await self.db.get(Quote, quote_id)
        if quote is None:
            raise NotFoundError("Quote not found.")
        quote.status = status
        await self.db.commit()
        return await self._load(quote_id)
