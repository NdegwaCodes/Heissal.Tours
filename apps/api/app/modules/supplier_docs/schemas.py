"""Schemas for supplier-document ingestion.

The confirm step is the point of this module, so the schemas are shaped around a
reviewer's decision rather than around the tables: a proposal carries what the
parser read, what it could not read, and how it compares with what is already
stored, so the person approving it can see all three at once.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.accommodations.models import RATE_KINDS


class SupplierDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    accommodation_id: uuid.UUID | None
    supplier_id: uuid.UUID | None
    original_filename: str
    content_type: str
    byte_size: int
    checksum: str
    status: str
    extraction_error: str | None
    notes: str | None
    uploaded_by: uuid.UUID | None
    created_at: datetime


class ProposedRate(BaseModel):
    """One candidate rate as the parser read it, plus what it could not read."""

    room_type: str | None = None
    meal_plan: str | None = None
    occupancy: int | None = None
    season_name: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    currency: str | None = None
    rate_per_night: Decimal | None = None
    warnings: list[str] = Field(default_factory=list)
    source_note: str | None = None
    page: int | None = None


class ExtractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    row_index: int
    status: str
    proposed: dict
    confidence: float | None
    reviewer_note: str | None
    created_rate_id: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None


class ExtractionSummary(BaseModel):
    """What one extraction pass found, for the screen that opens after upload."""

    document_id: uuid.UUID
    provider: str
    page_count: int
    total_rows: int
    complete_rows: int
    incomplete_rows: int
    warnings: list[str]
    needs_other_provider: bool


class ExtractHint(BaseModel):
    """What the uploader tells us that the document itself does not say.

    Residence category and rate kind are properties of the document, and the
    supplier discount is often only in the filename, so they are declared rather
    than parsed (design doc §5a).
    """

    residence_category: str | None = None
    rate_kind: str | None = None
    default_currency: str | None = None
    default_meal_plan: str | None = None
    supplier_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    vat_inclusive: bool = True
    vat_pct: Decimal = Field(default=Decimal("16"), ge=0, le=100)

    @field_validator("rate_kind")
    @classmethod
    def _known_kind(cls, v: str | None) -> str | None:
        if v is not None and v not in RATE_KINDS:
            raise ValueError(f"rate_kind must be one of {', '.join(RATE_KINDS)}")
        return v

    @field_validator("default_currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class ConfirmRow(BaseModel):
    """A reviewer's decision on one proposed row.

    Every field the parser proposed can be overridden here, because the reviewer
    is the authority and a confirm screen that cannot correct a misread value is
    a confirm screen that trains people to click through.
    """

    extraction_id: uuid.UUID
    accept: bool = True
    room_type_id: uuid.UUID | None = None
    meal_plan_id: uuid.UUID | None = None
    residence_category_id: uuid.UUID | None = None
    occupancy: int | None = Field(default=None, ge=1, le=12)
    season_name: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    currency: str | None = None
    rate_per_night: Decimal | None = Field(default=None, gt=0)
    rate_kind: str | None = None
    supplier_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    vat_inclusive: bool | None = None
    vat_pct: Decimal | None = Field(default=None, ge=0, le=100)
    child_min_age: int | None = Field(default=None, ge=0, le=25)
    child_max_age: int | None = Field(default=None, ge=0, le=25)
    child_rate: Decimal | None = Field(default=None, gt=0)
    reviewer_note: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class ConfirmDefaults(BaseModel):
    """Values applied to every row being confirmed, unless the row overrides it.

    This is what makes a partly-read document usable. Most sheets the parser
    only half-reads are missing the *same* field on every row — the residence
    category, the currency, or the occupancy — because the document never states
    it. Without shared defaults a reviewer would retype one value a hundred and
    fifty times, and a confirm screen that tedious is a confirm screen people
    stop reading.

    A row's own value always wins, so a default cannot silently overwrite
    something the parser did read.
    """

    room_type_id: uuid.UUID | None = None
    meal_plan_id: uuid.UUID | None = None
    residence_category_id: uuid.UUID | None = None
    occupancy: int | None = Field(default=None, ge=1, le=12)
    currency: str | None = None
    rate_kind: str | None = None
    supplier_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    vat_inclusive: bool | None = None
    vat_pct: Decimal | None = Field(default=None, ge=0, le=100)
    child_min_age: int | None = Field(default=None, ge=0, le=25)
    child_max_age: int | None = Field(default=None, ge=0, le=25)
    child_rate: Decimal | None = Field(default=None, gt=0)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("rate_kind")
    @classmethod
    def _known_kind(cls, v: str | None) -> str | None:
        if v is not None and v not in RATE_KINDS:
            raise ValueError(f"rate_kind must be one of {', '.join(RATE_KINDS)}")
        return v


class ConfirmRequest(BaseModel):
    rows: list[ConfirmRow] = Field(min_length=1)
    defaults: ConfirmDefaults | None = None


class ConfirmResultRow(BaseModel):
    extraction_id: uuid.UUID
    status: str
    rate_id: uuid.UUID | None = None
    error: str | None = None


class ConfirmResult(BaseModel):
    confirmed: int
    rejected: int
    failed: int
    rows: list[ConfirmResultRow]
