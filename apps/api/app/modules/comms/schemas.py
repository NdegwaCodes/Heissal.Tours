"""Schemas for the contact log (§5.3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CommunicationLog(BaseModel):
    """One call, email, message, meeting or note, as it is recorded."""

    #: Free text, normalised on the way in — see ``comms.rules`` for why the
    #: channel is not an enum and the direction is.
    channel: str | None = Field(default=None, max_length=40)
    #: inbound | outbound | internal. Refused rather than defaulted: a wrong
    #: direction decides whether the client counts as contacted and whether
    #: they count as having replied.
    direction: str
    #: **When it happened**, not when it is being typed. Defaults to now, which
    #: is the common case; the other common case is Friday afternoon writing up
    #: Tuesday.
    occurred_at: datetime | None = None
    subject_line: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)
    #: Did it connect? Leave unset where the question does not apply.
    reached: bool | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    #: A provider message id or a PBX call reference — the seam an integration
    #: lands on, and what makes an entry checkable against another system.
    external_ref: str | None = Field(default=None, max_length=200)

    #: Set the lead's next step in the same call. Here because the end of a
    #: conversation is the only moment anybody knows what it is, and a second
    #: screen for it is a step nobody takes. It writes the lead's own
    #: ``next_action_on``: there is one answer to "what happens next", not two.
    next_action_on: date | None = None
    next_action_note: str | None = None


class CommunicationAmend(BaseModel):
    """Fix a logged entry. Not its subject, and not its direction.

    An entry logged against the wrong lead is not a typo — it is a fact about
    another conversation, and the response time and chase count of two leads
    were computed from it. Void it and log it where it belongs.
    """

    subject_line: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    occurred_at: datetime | None = None
    reached: bool | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    external_ref: str | None = Field(default=None, max_length=200)


class CommunicationVoid(BaseModel):
    #: Required: "voided" is a fact nobody can act on, and the entry stays
    #: visible.
    reason: str = Field(min_length=1)


class CommunicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    subject: str
    subject_id: uuid.UUID
    channel: str
    direction: str
    occurred_at: datetime
    subject_line: str | None
    body: str
    reached: bool | None
    duration_minutes: int | None
    external_ref: str | None
    logged_by: uuid.UUID | None
    amended_at: datetime | None
    amended_by: uuid.UUID | None
    voided_at: datetime | None
    voided_by: uuid.UUID | None
    void_reason: str | None
    #: When the row was written, which is not when the conversation happened.
    created_at: datetime


class ContactedRead(BaseModel):
    """What a timeline adds up to."""

    entries: int
    #: Entries that are contact with the client. Internal notes and voided
    #: entries are excluded, because a lead whose only activity is three
    #: internal notes has not been contacted.
    contacts: int
    last_contact_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    #: Outbound attempts since the client last said anything.
    chases: int = 0
    by_channel: dict[str, int] = Field(default_factory=dict)
    by_direction: dict[str, int] = Field(default_factory=dict)
    unreached_calls: int = 0


class SilenceRead(BaseModel):
    """A conversation that has stopped, described and never concluded."""

    chases: int
    days: int
    since: datetime
    ever_replied: bool
    message: str


class TimelineRead(BaseModel):
    """Everything ever said about one thing, newest first.

    For a **lead** this is wider than the lead's own entries: it gathers the
    client it points at, the quotes raised from it and the bookings made from
    those. The conversation does not stop when a lead is won.
    """

    subject: str
    subject_id: uuid.UUID
    entries: list[CommunicationRead]
    summary: ContactedRead
    #: Hours from the enquiry arriving to the first word back, on a lead.
    #: ``null`` where nobody has answered it — which is an open sore, not a
    #: zero.
    first_response_hours: Decimal | None = None
    #: Present only when the caller's thresholds are met.
    gone_quiet: SilenceRead | None = None
