"""Schemas for crew and trip assignments (§8.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CrewBase(BaseModel):
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    #: ``driver``, ``guide`` or ``driver_guide`` — a list, because a
    #: driver-guide is one person and two rows would double-book them against
    #: themselves.
    roles: list[str] | None = None
    licence_number: str | None = Field(default=None, max_length=60)
    #: The day it runs out, not a valid/invalid flag: a licence expiring in the
    #: middle of a safari passes every check made on the Monday.
    licence_expires_on: date | None = None
    guide_licence_number: str | None = Field(default=None, max_length=60)
    guide_licence_expires_on: date | None = None
    #: What they speak. The reason a particular person goes on a particular
    #: trip more often than any other.
    languages: list[str] | None = None
    supplier_id: uuid.UUID | None = None
    notes: str | None = None


class CrewCreate(CrewBase):
    name: str = Field(min_length=1, max_length=200)
    roles: list[str] = Field(min_length=1)


class CrewUpdate(CrewBase):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class CrewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    roles: list[str]
    licence_number: str | None
    licence_expires_on: date | None
    guide_licence_number: str | None
    guide_licence_expires_on: date | None
    languages: list[str]
    supplier_id: uuid.UUID | None
    is_active: bool
    notes: str | None
    created_at: datetime


class AssignmentCreate(BaseModel):
    """Put one vehicle **or** one person on a booking."""

    vehicle_id: uuid.UUID | None = None
    crew_id: uuid.UUID | None = None
    #: What they are doing. Required where the person is down as more than one
    #: thing: a trip sheet has to name one.
    role: str | None = None
    #: Defaults to the booking's own dates. Widen it where the vehicle leaves
    #: the night before — a fleet calendar that says otherwise will hand it to
    #: somebody else on the Sunday.
    starts_on: date | None = None
    ends_on: date | None = None
    notes: str | None = None
    #: Required to push past a clash. Two trips over the same days is one trip
    #: that does not happen, so the default is no — but an operator who knows
    #: the first is about to be cancelled needs a way through that leaves their
    #: name on it.
    override_reason: str | None = None


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    booking_id: uuid.UUID
    vehicle_id: uuid.UUID | None
    crew_id: uuid.UUID | None
    role: str
    starts_on: date
    ends_on: date
    notes: str | None
    override_reason: str | None
    assigned_by: uuid.UUID | None
    created_at: datetime


class ClashRead(BaseModel):
    code: str
    message: str
    reference: str = ""
    blocking: bool = True


class AssignmentMade(BaseModel):
    """The assignment, plus anything worth an operator's eye.

    The advisories are returned rather than swallowed: a same-day handover is
    not a refusal, but a tight one and a comfortable one look identical once
    the response says only "created".
    """

    assignment: AssignmentRead
    advisories: list[ClashRead] = Field(default_factory=list)


class GapRead(BaseModel):
    code: str
    message: str
    #: Days until departure, so a board can be read soonest-first.
    days: int = 0


class RosterRead(BaseModel):
    """Who and what is on a trip."""

    vehicles: list[str] = Field(default_factory=list)
    drivers: list[str] = Field(default_factory=list)
    guides: list[str] = Field(default_factory=list)
    seats: int = 0


class ConcernRead(BaseModel):
    code: str
    message: str
    supplier: str = ""
    days: int = 0
    variance_pct: Decimal | None = None


class DepartureRead(BaseModel):
    """One trip on the departure board."""

    booking_id: uuid.UUID
    reference: str
    arrival_date: date
    departure_date: date
    pax_count: int
    status: str
    roster: RosterRead
    #: What still stands between this booking and a trip that can leave.
    #: Empty is the answer an operator wants, and a missing **guide** is
    #: deliberately not in here — whether a trip needs one depends on what the
    #: client asked for.
    gaps: list[GapRead] = Field(default_factory=list)
    #: Suppliers who have not confirmed (§8.3). Kept apart from ``gaps``
    #: because they are somebody else's answer to chase rather than something
    #: to allocate — and because a board showing a vehicle, a driver and enough
    #: seats, all green, with no reservation at the lodge is the failure this
    #: column exists for.
    supply: list[ConcernRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# What the vehicle actually did (§8.2)
# --------------------------------------------------------------------------- #


class TripLogOpen(BaseModel):
    """Record a vehicle leaving."""

    odometer_out_km: Decimal = Field(ge=0)
    started_on: date | None = None
    #: Who was actually at the wheel, which is not always who was rostered.
    driver_id: uuid.UUID | None = None
    notes: str | None = None


class TripLogClose(BaseModel):
    odometer_in_km: Decimal = Field(ge=0)
    ended_on: date | None = None


class FuelFillCreate(BaseModel):
    """One fuel receipt. Litres **and** money, both off the paper.

    Neither is derived from the other: a litre price computed by division loses
    the partial fill, the price that changed mid-trip, and the pump out at Voi
    charging a premium — which is what somebody is actually looking for when
    they ask why a trip cost what it did.
    """

    litres: Decimal = Field(gt=0)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    bought_on: date | None = None
    station: str | None = Field(default=None, max_length=160)
    receipt_ref: str | None = Field(default=None, max_length=80)
    notes: str | None = None


class FuelFillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    litres: Decimal
    amount: Decimal
    currency: str
    bought_on: date
    station: str | None
    receipt_ref: str | None
    notes: str | None


class ActualRead(BaseModel):
    """One trip measured, beside what the pricing model predicted."""

    distance_km: Decimal | None = None
    litres: Decimal = Decimal(0)
    fuel_cost: Decimal = Decimal(0)
    currency: str = ""
    #: The vehicle's configured figure — the one every quote is priced on.
    model_kmpl: Decimal | None = None
    #: What it managed. ``null`` on a trip too short for the number to mean
    #: anything: a 40 km transfer with one tankful is arithmetic on noise.
    actual_kmpl: Decimal | None = None
    #: What the model says this distance should have taken.
    model_litres: Decimal | None = None
    #: Positive means it did better than the model — the harmless direction.
    variance_pct: Decimal | None = None


class TripLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    assignment_id: uuid.UUID
    vehicle_id: uuid.UUID
    booking_id: uuid.UUID
    odometer_out_km: Decimal
    odometer_in_km: Decimal | None
    started_on: date
    ended_on: date | None
    driver_id: uuid.UUID | None
    notes: str | None
    distance_km: Decimal | None = None
    is_open: bool = False
    fills: list[FuelFillRead] = Field(default_factory=list)


class TripLogOpened(BaseModel):
    """The log, plus anything the odometer implies.

    The observations are returned rather than swallowed: kilometres between
    this reading and the vehicle's last return are not an error — repositioning,
    a service run, somebody's weekend — but a response that says only "created"
    buries them.
    """

    log: TripLogRead
    observations: list[str] = Field(default_factory=list)


class FindingRead(BaseModel):
    code: str
    message: str
    trips: int = 0
    model_kmpl: Decimal | None = None
    actual_kmpl: Decimal | None = None
    variance_pct: Decimal | None = None


class FleetTruthRead(BaseModel):
    """One vehicle's measured consumption over a run of trips.

    Pooled — total kilometres over total litres — rather than a mean of
    per-trip ratios, which would let one 120 km transfer weigh as much as a
    1,400 km circuit.
    """

    vehicle: str
    trips: int = 0
    distance_km: Decimal = Decimal(0)
    litres: Decimal = Decimal(0)
    fuel_cost: Decimal = Decimal(0)
    currency: str = ""
    model_kmpl: Decimal | None = None
    actual_kmpl: Decimal | None = None
    #: Reported, never applied. Changing ``fuel_consumption_kmpl`` re-prices
    #: work in flight, and deciding that a fortnight of receipts is the new
    #: truth belongs to whoever will have to explain the margin.
    findings: list[FindingRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# The supplier side of a booking (§8.3)
# --------------------------------------------------------------------------- #


class SupplierBookingCreate(BaseModel):
    supplier_id: uuid.UUID
    #: What is being bought, in the operator's words — "3 x garden twin, HB".
    #: "Hotel" is not something anybody can confirm.
    service: str = Field(min_length=1, max_length=300)
    #: What the quote was costed at. **Typed off the contract, not derived**:
    #: the snapshot holds one figure for a whole option across every supplier
    #: on it, so a per-hotel split would be a guess dressed as a fact.
    expected_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    #: Default to the booking's own dates.
    check_in: date | None = None
    check_out: date | None = None
    #: The day this stops being a plan. Per row, because a Mara camp in August
    #: wanted it in February and a Diani hotel in May will take it on the
    #: Thursday.
    confirm_by: date | None = None
    notes: str | None = None


class SupplierBookingMove(BaseModel):
    status: str
    #: **Their** reference. Required to confirm: without one this is somebody's
    #: recollection of a phone call, and there is nothing for the hotel to look
    #: up while a family stands in the lobby.
    their_reference: str | None = Field(default=None, max_length=120)
    #: Required to cancel. The next person talking to this supplier needs to
    #: know whether we moved the dates or they let us down.
    reason: str | None = None
    on: date | None = None


class SupplierAmount(BaseModel):
    amount: Decimal = Field(ge=0)
    #: Refused where it differs from what was committed: what they invoiced is
    #: a fact and the exchange rate is a decision.
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    on: date | None = None


class SupplierBookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    booking_id: uuid.UUID
    supplier_id: uuid.UUID
    service: str
    status: str
    their_reference: str | None
    check_in: date | None
    check_out: date | None
    confirm_by: date | None
    expected_amount: Decimal
    #: ``null`` until the invoice arrives, which is a different thing from zero.
    invoiced_amount: Decimal | None
    settled_amount: Decimal | None
    currency: str
    requested_on: date | None
    confirmed_on: date | None
    settled_on: date | None
    cancel_reason: str | None
    notes: str | None
    created_at: datetime


class ExposureRead(BaseModel):
    """What is owed to this trip's suppliers.

    Per currency and never summed across them: a lodge billing in dollars
    beside a transfer company billing in shillings is the normal case here, and
    one added figure would be wrong in a way nobody can see.
    """

    expected: dict[str, Decimal] = Field(default_factory=dict)
    invoiced: dict[str, Decimal] = Field(default_factory=dict)
    settled: dict[str, Decimal] = Field(default_factory=dict)
    #: The invoice where there is one, else the budget — a supplier who has not
    #: invoiced is not a supplier who is owed nothing.
    owed: dict[str, Decimal] = Field(default_factory=dict)
    suppliers: int = 0
    unconfirmed: int = 0
    all_confirmed: bool = False


class SupplyPositionRead(BaseModel):
    exposure: ExposureRead
    #: Unconfirmed suppliers, invoices away from budget, and anybody still owed
    #: after the group went home.
    concerns: list[ConcernRead] = Field(default_factory=list)
