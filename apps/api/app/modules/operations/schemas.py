"""Schemas for crew and trip assignments (§8.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

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
