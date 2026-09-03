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
    QuoteCohort,
    QuoteCounter,
    QuoteLeg,
    QuoteOption,
    QuoteOptionLeg,
    QuoteTransport,
    QuoteTransportSegment,
    QuoteTraveller,
)
from app.modules.quotes.packages import Leg
from app.modules.quotes.packages import blocking as blocking_problems
from app.modules.quotes.packages import check as check_package
from app.modules.quotes.schemas import QuoteCreate
from app.modules.quotes.transport import check as check_transport
from app.modules.quotes.transport import segments_of
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
            pax_count=payload.pax_count,
            profit_pct=payload.profit_pct,
            contingency_pct=payload.contingency_pct,
            requested_meal_plan_id=payload.requested_meal_plan_id,
            document_title=payload.document_title,
            document_subtitle=payload.document_subtitle,
            created_by=actor_id,
        )
        for t in payload.travellers:
            quote.travellers.append(
                QuoteTraveller(traveller_type=t.traveller_type, age=t.age)
            )
        await self._attach_cohorts(quote, payload.cohorts)
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
        signatures: set[tuple] = set()
        for index, opt in enumerate(payload.options, start=1):
            self._check_package(
                opt,
                index=index,
                arrival=payload.arrival_date,
                departure=payload.departure_date,
                seen=signatures,
            )
            option = QuoteOption(
                accommodation_id=opt.accommodation_id,
                is_recommended=opt.is_recommended,
                # Ties keep the order the agent listed them in rather than
                # falling back to insertion order, which is not stable.
                sort_order=opt.sort_order or index,
                agent_cover_fee=opt.agent_cover_fee,
                chef_fee_per_meal=opt.chef_fee_per_meal,
                manual_meal_cost=opt.manual_meal_cost,
                is_comparable=opt.is_comparable,
                notes=opt.notes,
            )
            for entry in opt.legs:
                option.legs.append(
                    QuoteOptionLeg(
                        sequence=entry.sequence,
                        destination_id=entry.destination_id,
                        accommodation_id=entry.accommodation_id,
                        requested_meal_plan_id=entry.requested_meal_plan_id,
                        check_in=entry.check_in,
                        check_out=entry.check_out,
                    )
                )
            quote.options.append(option)
        for tr in payload.transport:
            quote.transport.append(
                QuoteTransport(
                    vehicle_id=tr.vehicle_id,
                    estimated_km=tr.estimated_km,
                    days=tr.days,
                )
            )
        for seg in payload.transport_segments:
            quote.transport_segments.append(
                QuoteTransportSegment(
                    sequence=seg.sequence,
                    kind=seg.kind,
                    mode=seg.mode,
                    travel_class=seg.travel_class,
                    destination_id=seg.destination_id,
                    vehicle_id=seg.vehicle_id,
                    vehicle_type=seg.vehicle_type,
                    units=seg.units,
                    travel_date=seg.travel_date,
                    is_optional=seg.is_optional,
                    is_vvip=seg.is_vvip,
                    description=seg.description,
                )
            )
        self._check_transport(quote)

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

    def _check_package(
        self,
        option,
        *,
        index: int,
        arrival: date,
        departure: date,
        seen: set[tuple],
    ) -> None:
        """Refuse a package that is not a trip somebody could take (§3.9).

        Contiguity is checked **at creation**, not only at readiness, because a
        stored package with a night missing is a quote that prices cleanly and
        is wrong: the per-person figure looks entirely normal whether or not the
        client has a bed on the third night. Readiness re-checks it — an agent
        can edit dates afterwards — but there is no reason to accept it in the
        first place.

        Minimum stay is deliberately *not* checked here. It depends on the rates
        on file, which is pricing's job, so it is enforced at readiness where the
        rates have been read.
        """
        if not option.legs:
            return

        problems = check_package(
            [
                Leg(
                    sequence=entry.sequence,
                    destination=str(entry.destination_id),
                    check_in=entry.check_in,
                    check_out=entry.check_out,
                )
                for entry in option.legs
            ],
            arrival=arrival,
            departure=departure,
        )
        if fatal := blocking_problems(problems):
            raise AppError(
                f"Option {index} is not a valid package: "
                + " ".join(f"({p.code}) {p.message}" for p in fatal)
            )

        # What the dropped uniqueness constraint used to mean, expressed over the
        # thing that actually has to be distinct. Two packages may share a
        # property — Nairobi then Mara against Nairobi then Amboseli — but two
        # identical leg sequences are the same offer listed twice.
        signature = tuple(
            (entry.sequence, entry.accommodation_id, entry.check_in, entry.check_out)
            for entry in sorted(option.legs, key=lambda e: e.sequence)
        )
        if signature in seen:
            raise AppError(
                f"Option {index} repeats a package already on this quote — the "
                "same properties on the same dates. Offer it once."
            )
        seen.add(signature)

    @staticmethod
    def _check_transport(quote: Quote) -> None:
        """Refuse transport that could not be sold as typed (§3.10).

        Only the blocking faults — a mode we hold no licence for, a kind
        nothing knows how to price, a rail leg without the transfers it drags
        with it. The advisory ones (a shortfall of movements, a flight to name,
        VVIP inside the package) belong at readiness, where the agent is asking
        "is this ready to send" rather than "did that save".

        Checked here as well as at readiness for the reason packages are: a
        stored fault prices cleanly and wrongly, and the earlier it is refused
        the less there is to unpick.
        """
        problems = blocking_problems(
            check_transport(
                segments_of(quote.transport_segments),
                legs=max(
                    [len(option.legs) for option in quote.options if option.legs]
                    or [1]
                ),
            )
        )
        if problems:
            raise AppError(
                "This quote's transport cannot be sold as entered: "
                + " ".join(f"({p.code}) {p.message}" for p in problems)
            )

    async def _attach_cohorts(self, quote: Quote, rows: list) -> None:
        """Validate and attach the group vector (§3.8).

        Two checks the database cannot make. Every residence category has to
        exist *and* have a billing currency, because a cohort with no currency
        produces bare numbers rather than prices. And where ``pax_count`` is also
        given it has to agree with the cohorts: the cohorts win either way, but
        a quote whose two headcounts disagree is a data-entry error, not a
        precedence question, and letting it through means someone later reads
        the wrong one and cannot tell.
        """
        if not rows:
            return

        wanted = {row.residence_category_id for row in rows}
        found = {
            category.id: category
            for category in (
                await self.db.execute(
                    select(ResidenceCategory).where(ResidenceCategory.id.in_(wanted))
                )
            )
            .scalars()
            .all()
        }
        if missing := wanted - set(found):
            raise NotFoundError(
                f"{len(missing)} cohort(s) name a residence category that does "
                "not exist."
            )
        if blank := sorted(
            found[id_].key for id_ in wanted if not found[id_].default_currency_code
        ):
            raise AppError(
                "These residence categories have no default currency, so "
                f"travellers in them cannot be priced: {', '.join(blank)}."
            )

        total = sum(row.headcount for row in rows)
        if quote.pax_count is not None and quote.pax_count != total:
            raise AppError(
                f"pax_count is {quote.pax_count} but the cohorts add up to "
                f"{total}. Send one or the other, or make them agree."
            )

        for row in rows:
            quote.cohorts.append(
                QuoteCohort(
                    residence_category_id=row.residence_category_id,
                    traveller_type=row.traveller_type,
                    headcount=row.headcount,
                )
            )

    async def update_quote(self, quote_id: uuid.UUID, patch: dict) -> Quote:
        quote = await self.db.get(Quote, quote_id)
        if quote is None:
            raise NotFoundError("Quote not found.")
        for name, value in patch.items():
            setattr(quote, name, value)
        await self.db.commit()
        return await self._load(quote_id)

    async def set_status(self, quote_id: uuid.UUID, status: str) -> Quote:
        quote = await self.db.get(Quote, quote_id)
        if quote is None:
            raise NotFoundError("Quote not found.")
        quote.status = status
        await self.db.commit()
        return await self._load(quote_id)
