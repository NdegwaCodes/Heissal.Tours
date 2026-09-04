"""Schemas for proposal copy and its review (§4.4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NarrativeGenerate(BaseModel):
    """Ask the configured provider for a draft.

    ``steer`` is the one free-text input, and it is the agent's: they have
    stayed at the property and know the thing worth saying. Everything else the
    provider is told comes from the catalogue, so a description cannot promise
    board we have no rate for.
    """

    steer: str = Field(default="", max_length=500)
    # A proposal's option page has room for a paragraph, not an essay (§3.11).
    words: int = Field(default=80, ge=20, le=250)


class NarrativeCompose(BaseModel):
    """An agent's own writing, onto the same review path."""

    text: str = Field(min_length=1, max_length=4000)


class NarrativeRevise(BaseModel):
    """Edit a draft. Approved copy is replaced, never edited — see the service."""

    text: str = Field(min_length=1, max_length=4000)


class NarrativeReview(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class NarrativeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    subject: str
    subject_id: uuid.UUID
    status: str
    text: str
    #: What produced it — a provider name, ``hand`` for an agent's writing, or
    #: ``<provider>+hand`` where a person edited a model's draft. On the record
    #: because only one of the two can be asked what it meant.
    provider: str
    model: str | None
    brief: dict
    created_by: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    superseded_at: datetime | None
    created_at: datetime
