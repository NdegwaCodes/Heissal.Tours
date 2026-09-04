"""Bookings, what is owed on them, and what has been paid (§7.1).

§5.1 gave a quote an outcome. This is where an accepted one leads: until now it
led nowhere at all — a deal could be won in the system and operations picked it
up in a spreadsheet.

Four decisions shape these tables.

**A booking is made against a version, not a quote.** ``quote_versions`` holds
the immutable snapshot the client actually received (§3.4); the quote it hangs
off keeps changing. So the booking points at the version, and the figure it
invoices is that version's own selling price. A booking whose total could move
because somebody re-priced the quote is not a booking.

**Creating one is an act, not a side effect.** Accepting a quote records a sale;
a booking is operational — it needs a schedule, a deposit and somebody's name
against it. Folding them together would mean every acceptance produced a
half-finished operational record, and the interesting thing about the pair is
the gap between them.

**The schedule is instalments, not a percentage.** "50% deposit" is policy;
"KES 223,750 due on 4 September" is what a client pays and what a bank
statement is reconciled against. The percentages live in config and are
resolved to rows at the moment of booking, which is also what makes them
frozen: changing the deposit policy next month must not restate an invoice
already sent.

**Payments are recorded, never inferred.** A payment row is a thing that
happened, with a method and a reference an operator can find in a statement.
There is no integration here yet: M-Pesa is a provider that will land behind
the same rows, and a booking that trusted a callback more than a reconciled
statement would be a booking nobody could audit.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: provisional — held, nothing paid, and the suppliers are not confirmed.
#: confirmed — the deposit has landed and the trip is on.
#: cancelled — it is not happening; the row stays, because what was owed and
#: what was paid are the two facts a refund argument turns on.
#: completed — they have travelled.
PROVISIONAL = "provisional"
CONFIRMED = "confirmed"
CANCELLED = "cancelled"
COMPLETED = "completed"
BOOKING_STATUSES = (PROVISIONAL, CONFIRMED, CANCELLED, COMPLETED)

#: A booking that is neither cancelled nor finished holds the trip, and a quote
#: may have only one of those at a time.
ACTIVE_STATUSES = (PROVISIONAL, CONFIRMED)

#: How the money arrived. Free-ish but conventional, and normalised at the
#: boundary: reconciling a statement means grouping by method, and "M-Pesa",
#: "mpesa" and "MPESA " are one method.
PAYMENT_METHODS = ("mpesa", "bank_transfer", "card", "cash", "cheque", "other")


class BookingCounter(Base):
    """Per-year sequence behind ``HTB-YYYY-NNNN``.

    Its own counter rather than sharing the quotes' one: a booking reference and
    a quote number are different things on different pieces of paper, and
    sharing a sequence would make the booking references sparse and unguessable
    in a way that helps nobody.
    """

    __tablename__ = "booking_counters"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Booking(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(
            "status in ('provisional', 'confirmed', 'cancelled', 'completed')",
            name="ck_booking_status",
        ),
        CheckConstraint("total_amount >= 0", name="ck_booking_total_positive"),
        CheckConstraint("departure_date > arrival_date", name="ck_booking_dates"),
        Index("ix_booking_quote", "quote_id"),
        Index("ix_booking_status_dates", "status", "arrival_date"),
    )

    reference: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quotes.id", ondelete="RESTRICT"), nullable=False
    )
    #: The immutable version the client accepted. What the booking invoices
    #: comes from here, so re-pricing the quote afterwards cannot move it.
    quote_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quote_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Which option they took. Denormalised from the quote because the booking
    #: is the operational record and an operator should not have to join back
    #: through a sales table to find out which hotel to call.
    option_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("quote_options.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=PROVISIONAL, index=True, nullable=False
    )

    # -- frozen at the moment of booking ------------------------------------ #
    # Copied rather than joined, deliberately. These are what the trip IS, and
    # an operations screen that showed different dates because somebody edited
    # the quote would be worse than useless.
    arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    pax_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why it was cancelled. No cancellation *charge* is computed anywhere: the
    #: ladder ("30 days out, 50% retained") is commercial policy nobody has
    #: given us, and inventing one would put a figure on an invoice that no
    #: contract supports. Recorded here so the conversation has a record.
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    instalments: Mapped[list[BookingInstalment]] = relationship(
        "BookingInstalment",
        back_populates="booking",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="BookingInstalment.sort_order",
    )
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="booking",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Payment.paid_on",
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class BookingInstalment(UUIDPKMixin, TimestampMixin, Base):
    """One thing the client owes on one date.

    Rows rather than a deposit percentage on the booking, because a percentage
    is policy and this is an invoice line. It also makes the schedule frozen:
    changing the deposit rule next month cannot restate an instalment already
    sent to somebody.
    """

    __tablename__ = "booking_instalments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_instalment_amount_positive"),
        Index("ix_instalment_due", "due_on"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: What a client reads on a statement: "Deposit", "Balance".
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    #: Held on the row rather than read from the booking: an instalment is an
    #: invoice line, and an invoice line with no currency on it is a number.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    booking: Mapped[Booking] = relationship("Booking", back_populates="instalments")


class Payment(UUIDPKMixin, TimestampMixin, Base):
    """Money that actually arrived.

    Recorded, never inferred. There is no payment integration yet and this is
    the shape one will land behind: an M-Pesa callback becomes a row here with
    its own reference, and an operator still reconciles it against a statement —
    because a booking that trusted a callback over a statement is a booking
    nobody can audit.
    """

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        Index("ix_payment_booking", "booking_id", "paid_on"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    #: The M-Pesa code, the bank reference, the cheque number — whatever finds
    #: this payment in somebody else's system. The single most useful field on
    #: the row when a client says they have paid and we cannot see it.
    reference: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    #: Which instalment it was against, where it is that clean. Nullable
    #: because real payments are not: clients pay round numbers, pay late, and
    #: pay two instalments at once.
    instalment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("booking_instalments.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    booking: Mapped[Booking] = relationship("Booking", back_populates="payments")
