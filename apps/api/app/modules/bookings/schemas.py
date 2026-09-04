"""Schemas for bookings, instalments and payments (§7.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BookingCreate(BaseModel):
    """Book the accepted option on a quote.

    Nothing else is asked for: the dates, the headcount, the option and the
    figure all come from the version the client accepted (§3.4). A booking form
    that let somebody retype the total would be a form that let somebody book a
    trip at a price nobody quoted.
    """

    notes: str | None = Field(default=None, max_length=2000)


class InstalmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str
    due_on: date
    amount: Decimal
    currency: str
    sort_order: int


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount: Decimal
    currency: str
    paid_on: date
    method: str
    #: The M-Pesa code, bank reference or cheque number — whatever finds this
    #: payment in somebody else's system.
    reference: str | None
    instalment_id: uuid.UUID | None
    notes: str | None
    recorded_by: uuid.UUID | None
    created_at: datetime


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    paid_on: date
    method: str = Field(default="other", max_length=20)
    #: Must match the booking's currency. A payment in another one is refused
    #: rather than converted: what cleared is a fact and the rate is a
    #: decision, and the decision belongs to whoever reconciles the statement.
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    reference: str | None = Field(default=None, max_length=120)
    #: Optional, because real payments do not line up with instalments:
    #: clients pay round numbers, pay late, and pay two at once.
    instalment_id: uuid.UUID | None = None
    notes: str | None = None


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reference: str
    quote_id: uuid.UUID
    quote_version_id: uuid.UUID
    option_id: uuid.UUID | None
    client_id: uuid.UUID
    status: str
    arrival_date: date
    departure_date: date
    pax_count: int
    total_amount: Decimal
    currency: str
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    completed_at: datetime | None
    notes: str | None
    created_at: datetime
    instalments: list[InstalmentRead] = Field(default_factory=list)
    payments: list[PaymentRead] = Field(default_factory=list)


class ScheduleLineRead(BaseModel):
    """An instalment as the position reports it, without an id.

    The pure layer works in amounts and dates, not rows, and inventing a UUID
    for a line the caller cannot fetch would be handing them an identifier that
    resolves to nothing. The schedule endpoint returns the real rows.
    """

    label: str
    due_on: date
    amount: Decimal
    currency: str


class OwedRead(BaseModel):
    """Where a booking stands, in money (§7.1)."""

    total: Decimal
    paid: Decimal
    #: What to put on a statement. Floored at zero — an overpayment is a credit,
    #: not a negative bill, and "you owe minus four thousand" is not a sentence
    #: a client should read.
    balance: Decimal
    #: The credit that is not on the statement, where there is one.
    overpaid: Decimal
    currency: str
    is_settled: bool
    overdue: list[ScheduleLineRead] = Field(default_factory=list)
    next_due: ScheduleLineRead | None = None


class BookingDueRead(BaseModel):
    """One booking on the operations list, and its position."""

    booking: BookingRead
    owed: OwedRead


class CancelBooking(BaseModel):
    """Stop a booking, with the reason on the record.

    No charge is computed: the cancellation ladder is commercial policy nobody
    has given us, and a plausible invented figure on a refund looks as though
    it came from a contract.
    """

    reason: str = Field(min_length=1, max_length=2000)
