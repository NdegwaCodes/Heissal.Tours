"""Turning an accepted quote into a booking, and tracking what is paid (§7.1).

The rules are in :mod:`app.modules.bookings.schedule`; this is the half that
talks to the database. What it will and will not do:

**It will only book an accepted quote.** §5.1 records the sale; this is the
operational record that follows it, and creating one from a quote nobody has
accepted would put a trip in the operations list that no client has agreed to.

**It books the version, not the quote.** The total comes from the immutable
snapshot the client received (§3.4). A booking whose figure could move because
somebody re-priced the quote is not a booking.

**It will not hold one trip twice.** A quote may be re-booked after a
cancellation — clients come back — but only one active booking at a time.

**It computes no cancellation charge.** The ladder is commercial policy nobody
has given us, and a plausible invented figure on a refund looks like it came
from a contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.bookings.models import (
    ACTIVE_STATUSES,
    CANCELLED,
    COMPLETED,
    CONFIRMED,
    PAYMENT_METHODS,
    PROVISIONAL,
    Booking,
    BookingCounter,
    BookingInstalment,
    Payment,
)
from app.modules.bookings.schedule import (
    Instalment,
    Owed,
    ScheduleRefused,
    build,
    is_confirmable,
    owed,
)
from app.modules.pricing.service import PricingConfigService
from app.modules.quotes.models import Quote, QuoteVersion
from app.modules.quotes.outcomes import ACCEPTED


def normalise_method(value: str | None) -> str:
    """A payment method as a statement should group it.

    "M-Pesa", "mpesa" and "MPESA " are one method, and reconciliation means
    grouping by it. Unknown values are kept rather than refused — a new payment
    channel should not need a deploy — but they are lower-cased so the report
    does not grow three rows for one thing.
    """
    cleaned = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or "other"


class BookingNumberService:
    """Allocates ``HTB-<year>-<NNNN>`` from a locked per-year counter.

    The same shape as the quote numbers (``HTQ-``), including the row lock, so
    two operators booking at once cannot be handed the same reference. Its own
    sequence: a booking reference and a quote number appear on different pieces
    of paper, and sharing one would make both sparse for no benefit.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def next_reference(self, year: int) -> str:
        counter = await self.db.get(BookingCounter, year, with_for_update=True)
        if counter is None:
            self.db.add(BookingCounter(year=year, last_value=0))
            await self.db.flush()
            counter = await self.db.get(BookingCounter, year, with_for_update=True)
        assert counter is not None  # just inserted and locked
        counter.last_value += 1
        await self.db.flush()
        return f"HTB-{year}-{counter.last_value:04d}"


class BookingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- creating ------------------------------------------------------------- #

    async def create_from_quote(
        self,
        quote_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
        notes: str | None = None,
        today: date | None = None,
    ) -> Booking:
        """Book the accepted option on a quote, with its payment schedule.

        Everything the operations team needs is copied onto the booking rather
        than joined: the dates, the headcount, the total and the currency. An
        operations screen showing different dates because somebody edited the
        quote afterwards would be worse than useless.
        """
        when = today or date.today()
        quote = (
            await self.db.execute(select(Quote).where(Quote.id == quote_id))
        ).scalar_one_or_none()
        if quote is None:
            raise NotFoundError("Quote not found.")
        if quote.status != ACCEPTED:
            raise AppError(
                f"This quote is {quote.status}, not accepted. Record the "
                f"client's acceptance first — a booking is the operational "
                f"record of a sale, and there is no sale yet."
            )
        if quote.current_version_id is None:
            raise AppError(
                "This quote has no issued version, so there is no figure to "
                "invoice. Issue it, then accept it, then book it."
            )
        existing = (
            (
                await self.db.execute(
                    select(Booking).where(
                        Booking.quote_id == quote_id,
                        Booking.status.in_(ACTIVE_STATUSES),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise AppError(
                f"This quote is already booked as {existing.reference}. One "
                f"trip cannot be held twice — cancel that booking first if it "
                f"is being replaced."
            )

        version = await self.db.get(QuoteVersion, quote.current_version_id)
        if version is None:
            raise NotFoundError("The quote's issued version is missing.")

        cfg = await PricingConfigService(self.db).get()
        try:
            instalments = build(
                Decimal(version.selling_price),
                version.currency,
                deposit_pct=cfg.deposit_pct,
                travel_from=quote.arrival_date,
                balance_due_days_before=cfg.balance_due_days_before_travel,
                today=when,
            )
        except ScheduleRefused as exc:
            raise AppError(str(exc)) from exc

        booking = Booking(
            reference=await BookingNumberService(self.db).next_reference(when.year),
            quote_id=quote.id,
            quote_version_id=version.id,
            option_id=quote.selected_option_id,
            client_id=quote.client_id,
            status=PROVISIONAL,
            arrival_date=quote.arrival_date,
            departure_date=quote.departure_date,
            # The headcount pricing used, which for a cohort quote is not on
            # the quote row at all (§3.8) — the version's snapshot is where it
            # was frozen.
            pax_count=int((version.snapshot or {}).get("pax_count") or quote.pax_count or 0),
            total_amount=Decimal(version.selling_price),
            currency=version.currency.upper(),
            notes=notes,
            created_by=actor_id,
        )
        for instalment in instalments:
            booking.instalments.append(
                BookingInstalment(
                    label=instalment.label,
                    due_on=instalment.due_on,
                    amount=instalment.amount,
                    currency=instalment.currency,
                    sort_order=instalment.sort_order,
                )
            )
        self.db.add(booking)
        await self.db.commit()
        return await self.get(booking.id)

    async def get(self, booking_id: uuid.UUID) -> Booking:
        booking = (
            await self.db.execute(select(Booking).where(Booking.id == booking_id))
        ).scalar_one_or_none()
        if booking is None:
            raise NotFoundError("Booking not found.")
        return booking

    # -- money ---------------------------------------------------------------- #

    async def record_payment(
        self,
        booking_id: uuid.UUID,
        *,
        amount: Decimal,
        paid_on: date,
        method: str | None,
        currency: str | None = None,
        reference: str | None = None,
        instalment_id: uuid.UUID | None = None,
        notes: str | None = None,
        actor_id: uuid.UUID | None = None,
        today: date | None = None,
    ) -> Booking:
        """Record money that arrived, and confirm the booking if it is enough.

        **The currency must match the booking's.** A payment in another currency
        is a real thing that happens and it is not something to convert quietly
        at today's rate: the amount that clears is a fact, the rate is a
        decision, and one of them belongs to whoever reconciles the statement.
        So it is refused with that said, rather than accepted and silently
        converted.

        Confirming on the **deposit** rather than the balance, because
        confirming is telling the suppliers it is happening and that is what a
        deposit buys.
        """
        booking = await self.get(booking_id)
        if booking.status == CANCELLED:
            raise AppError(
                f"{booking.reference} is cancelled. Record a refund against it "
                f"rather than a payment, or reinstate it first."
            )
        code = (currency or booking.currency).upper()
        if code != booking.currency.upper():
            raise AppError(
                f"{booking.reference} is invoiced in {booking.currency} and "
                f"this payment is in {code}. What cleared is a fact and the "
                f"exchange rate is a decision — record the amount in "
                f"{booking.currency} that reached the account, and keep the "
                f"rate you used in the notes."
            )
        if instalment_id is not None:
            target = await self.db.get(BookingInstalment, instalment_id)
            if target is None or target.booking_id != booking.id:
                raise NotFoundError(
                    "That instalment does not belong to this booking."
                )

        self.db.add(
            Payment(
                booking_id=booking.id,
                amount=amount,
                currency=code,
                paid_on=paid_on,
                method=normalise_method(method),
                reference=reference,
                instalment_id=instalment_id,
                notes=notes,
                recorded_by=actor_id,
            )
        )
        await self.db.flush()
        await self.db.refresh(booking)

        if booking.status == PROVISIONAL and is_confirmable(
            [_instalment(row) for row in booking.instalments],
            [row.amount for row in booking.payments],
        ):
            booking.status = CONFIRMED
            booking.confirmed_at = datetime.now(UTC)
        await self.db.commit()
        return await self.get(booking_id)

    async def position(
        self, booking_id: uuid.UUID, *, today: date | None = None
    ) -> Owed:
        """What is paid, what is owed, what is overdue, and what is overpaid."""
        booking = await self.get(booking_id)
        return owed(
            [_instalment(row) for row in booking.instalments],
            [row.amount for row in booking.payments],
            total=booking.total_amount,
            currency=booking.currency,
            today=today or date.today(),
        )

    # -- lifecycle ------------------------------------------------------------ #

    async def cancel(
        self,
        booking_id: uuid.UUID,
        *,
        reason: str,
        actor_id: uuid.UUID | None = None,
    ) -> Booking:
        """Stop a booking, with the reason on the record.

        No charge is computed. The cancellation ladder is commercial policy
        nobody has given us, and a plausible invented figure on a refund looks
        as though it came from a contract. What is paid and what was owed stay
        exactly as they are, which is what a refund conversation actually needs.
        """
        booking = await self.get(booking_id)
        if booking.status == CANCELLED:
            return booking
        if booking.status == COMPLETED:
            raise AppError(
                f"{booking.reference} is completed — they have travelled. A "
                f"trip that happened cannot be cancelled; record what needs "
                f"recording against it instead."
            )
        if not (reason or "").strip():
            raise AppError("Say why the booking was cancelled.")
        booking.status = CANCELLED
        booking.cancelled_at = datetime.now(UTC)
        booking.cancellation_reason = reason
        await self.db.commit()
        return await self.get(booking_id)

    async def complete(
        self, booking_id: uuid.UUID, *, today: date | None = None
    ) -> Booking:
        """Mark a trip as travelled.

        Refused before the departure date: a trip cannot have happened while it
        is still in the future, and a completed booking is what every
        post-travel report will count.
        """
        booking = await self.get(booking_id)
        when = today or date.today()
        if booking.status == CANCELLED:
            raise AppError(
                f"{booking.reference} was cancelled, so it did not happen."
            )
        if when < booking.departure_date:
            raise AppError(
                f"{booking.reference} departs on {booking.departure_date}. A "
                f"trip cannot be completed before it has finished."
            )
        booking.status = COMPLETED
        booking.completed_at = datetime.now(UTC)
        await self.db.commit()
        return await self.get(booking_id)

    async def due(
        self, *, today: date | None = None, within_days: int = 7
    ) -> list[tuple[Booking, Owed]]:
        """Active bookings with something overdue or falling due soon.

        The operations equivalent of §5.2's morning list: an unpaid balance
        two weeks before travel is a phone call, and a booking that reaches
        the airport unpaid is a loss.
        """
        when = today or date.today()
        rows = list(
            (
                await self.db.execute(
                    select(Booking).where(Booking.status.in_(ACTIVE_STATUSES))
                )
            )
            .scalars()
            .all()
        )
        out: list[tuple[Booking, Owed]] = []
        for booking in rows:
            position = owed(
                [_instalment(one) for one in booking.instalments],
                [one.amount for one in booking.payments],
                total=booking.total_amount,
                currency=booking.currency,
                today=when,
            )
            if position.is_settled:
                continue
            soon = position.next_due is not None and (
                position.next_due.due_on - when
            ).days <= within_days
            if position.overdue or soon:
                out.append((booking, position))
        out.sort(
            key=lambda pair: (
                pair[1].next_due.due_on if pair[1].next_due else pair[0].arrival_date
            )
        )
        return out


def _instalment(row: BookingInstalment) -> Instalment:
    return Instalment(
        label=row.label,
        due_on=row.due_on,
        amount=Decimal(row.amount),
        currency=row.currency,
        sort_order=row.sort_order,
    )


__all__ = [
    "PAYMENT_METHODS",
    "BookingNumberService",
    "BookingService",
    "normalise_method",
]
