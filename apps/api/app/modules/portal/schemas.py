"""Schemas for client portal access (§7.2)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class GrantCreate(BaseModel):
    #: Who it is for, in the agent's words — "Mrs Achieng", "the group's
    #: organiser". A booking can have several, and revoking one must not lock
    #: the other out.
    label: str | None = Field(default=None, max_length=160)
    #: Defaults to a while after they travel, since the statement and the
    #: receipts are wanted after the trip.
    expires_on: date | None = None


class GrantRevoke(BaseModel):
    #: Required. A link gets forwarded into a family group chat, and the next
    #: agent needs to tell a leak from a mistake.
    reason: str = Field(min_length=1)


class GrantRead(BaseModel):
    """A grant as an agent sees it. **Never** the token."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    booking_id: uuid.UUID
    label: str | None
    expires_on: date
    revoked_at: datetime | None
    revoke_reason: str | None
    #: Whether the client has opened it, and when they last did — "did they
    #: even see the itinerary?" is a real question.
    last_seen_at: datetime | None
    view_count: int
    created_at: datetime


class GrantIssued(GrantRead):
    """The one response that carries the token, at the moment it is created.

    There is no second way to get it: the table holds a hash, so a copy of the
    database is not a set of live links. An agent who needs to resend issues a
    new grant, which is one click and also revocable separately.
    """

    token: str
    #: The link to send. The token is in the **fragment**, which a browser
    #: never sends to a server — so it stays out of access logs and out of the
    #: Referer header when the client clicks through to an airline.
    url: str


# --------------------------------------------------------------------------- #
# What the client gets
# --------------------------------------------------------------------------- #


class MovementRead(BaseModel):
    label: str
    minutes: int | None = None


class DayRead(BaseModel):
    #: Serialised as ``date``. The attribute cannot be called that: a field
    #: named for its own type shadows the type while the annotations are
    #: resolved, and pydantic then cannot evaluate ``date | None``.
    model_config = ConfigDict(populate_by_name=True)

    number: int
    on: date | None = Field(default=None, alias="date")
    destination: str = ""
    property_name: str = ""
    board: str = ""
    movements: list[MovementRead] = Field(default_factory=list)
    excursions: list[str] = Field(default_factory=list)
    is_arrival: bool = False
    is_departure: bool = False
    has_night: bool = True


class StayRead(BaseModel):
    sequence: int
    destination: str = ""
    property_name: str = ""
    room_type: str = ""
    board: str = ""
    rooms: int = 0
    nights: int = 0


class TripRead(BaseModel):
    """The client's own trip.

    No cost and no margin — not filtered out, but never put in: the view model
    is built from an allow-list over the frozen snapshot, so a costing field
    added to the snapshot next month cannot appear here by accident. And only
    the option they accepted: a quote offers three to nine, and showing the two
    they turned down re-opens a decision they have paid a deposit on.
    """

    reference: str
    status: str
    arrival_date: date | None = None
    departure_date: date | None = None
    pax_count: int = 0
    #: What they agreed to pay, frozen onto the booking (§7.1) rather than read
    #: back off the snapshot — which is what stops a re-priced quote moving it.
    total: Decimal
    currency: str
    property_name: str = ""
    room_type: str = ""
    board: str = ""
    nights: int = 0
    #: The approved description of the property, as it stood when the proposal
    #: went out (§4.4).
    description: str | None = None
    stays: list[StayRead] = Field(default_factory=list)
    days: list[DayRead] = Field(default_factory=list)
    included: list[str] = Field(default_factory=list)


class InstalmentRead(BaseModel):
    label: str
    due_on: date
    amount: Decimal
    currency: str


class PaymentRead(BaseModel):
    """A payment the client can see. The internal note is not part of it."""

    amount: Decimal
    currency: str
    paid_on: date
    method: str
    reference: str | None = None


class StatementRead(BaseModel):
    """Where the booking stands, in money.

    ``balance`` is what to pay; ``overpaid`` is the credit that is not on it.
    Kept apart for §7.1's reason: "you owe minus four thousand shillings" is
    not a sentence a client should read.
    """

    reference: str
    currency: str
    total: Decimal
    paid: Decimal
    balance: Decimal
    overpaid: Decimal = Decimal(0)
    is_settled: bool = False
    schedule: list[InstalmentRead] = Field(default_factory=list)
    payments: list[PaymentRead] = Field(default_factory=list)
    overdue: list[InstalmentRead] = Field(default_factory=list)
    next_due: InstalmentRead | None = None
