"""Schemas for leads and the pipeline (§5.2)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LeadStageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str
    sort_order: int
    is_default: bool
    is_won: bool
    is_lost: bool
    description: str | None = None
    is_active: bool


class LeadStageUpdate(BaseModel):
    """Rename or reorder a stage.

    The key and the won/lost flags are not editable here. Every report asks
    "which stage means won" rather than comparing a name, so renaming is safe —
    but changing what a stage *means* changes history that has already been
    counted, and that is a migration-shaped decision rather than an edit.
    """

    name: str | None = Field(default=None, min_length=1, max_length=80)
    sort_order: int | None = Field(default=None, ge=0)
    description: str | None = None


class LeadBase(BaseModel):
    #: Free text, normalised on the way in — see the service for why it is not
    #: an enum.
    source: str | None = Field(default=None, max_length=40)
    source_detail: str | None = Field(default=None, max_length=160)
    client_id: uuid.UUID | None = None
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=50)
    owner_id: uuid.UUID | None = None
    destination_interest: str | None = Field(default=None, max_length=200)
    travel_from: date | None = None
    travel_to: date | None = None
    pax_estimate: int | None = Field(default=None, ge=1)
    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)
    next_action_on: date | None = None
    next_action_note: str | None = None
    notes: str | None = None


class LeadCreate(LeadBase):
    contact_name: str = Field(min_length=1, max_length=200)
    #: Omit to land at the stage marked as where new enquiries arrive.
    stage_id: uuid.UUID | None = None


class LeadUpdate(LeadBase):
    """Correct a lead's own fields. The stage moves through ``/move``.

    Two paths deliberately: everything here is a correction, while a stage
    change is an event with a time and an author. Folding them together would
    let a typo fix write a stage change that never happened into the history
    every pipeline report is built on.
    """

    contact_name: str | None = Field(default=None, min_length=1, max_length=200)
    lost_reason: str | None = None


class LeadMove(BaseModel):
    stage_id: uuid.UUID
    note: str | None = None
    #: Required by the rules when the target stage means lost.
    lost_reason: str | None = None
    #: Set the next step in the same call, since moving a lead forward is
    #: exactly when somebody knows what it is.
    next_action_on: date | None = None
    next_action_note: str | None = None


class LeadStageEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    at: datetime
    by: uuid.UUID | None
    note: str | None


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source: str
    source_detail: str | None
    client_id: uuid.UUID | None
    contact_name: str
    contact_email: str | None
    contact_phone: str | None
    stage_id: uuid.UUID
    stage_since: datetime
    owner_id: uuid.UUID | None
    destination_interest: str | None
    travel_from: date | None
    travel_to: date | None
    pax_estimate: int | None
    budget_amount: Decimal | None
    budget_currency: str | None
    next_action_on: date | None
    next_action_note: str | None
    lost_reason: str | None
    notes: str | None
    created_at: datetime
    #: The whole pipeline history, oldest first. It is the part worth reading:
    #: a current stage says where a lead is, and only the events say how long
    #: it took to get there.
    events: list[LeadStageEventRead] = Field(default_factory=list)


class AttentionRead(BaseModel):
    code: str
    message: str
    days: int = 0


class LeadAttentionRead(BaseModel):
    """One lead on the morning list, and every reason it is there."""

    lead: LeadRead
    reasons: list[AttentionRead]


class StageCountRead(BaseModel):
    stage: str
    name: str
    leads: int
    #: Median days the leads currently here have been here. Median for the
    #: §5.1 reason: one forgotten lead would move a mean into fiction.
    median_days: int | None = None


class SourceCountRead(BaseModel):
    source: str
    leads: int
    quoted: int
    won: int
    #: Won over **all** leads from this source, not over the quoted ones: what
    #: a marketing decision needs is enquiries in, bookings out.
    win_rate: Decimal | None = None


class PipelineRead(BaseModel):
    stages: list[StageCountRead]
    sources: list[SourceCountRead]
    #: Open leads' stated budgets, per currency, and never summed across them.
    #: A soft figure, labelled as one: it is what clients said.
    open_budget: dict[str, Decimal]
    open_leads: int
    won_leads: int
    lost_leads: int
    win_rate: Decimal | None = None
    #: What is wrong with the pipeline's own shape, if anything — no entry
    #: stage, nothing meaning won. Reported rather than raised, so a client
    #: mid-way through renaming their stages still gets their numbers.
    problems: list[str] = Field(default_factory=list)
