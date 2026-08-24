"""Supplier rate documents and their proposed extractions (Stage 3.1).

Hotel rate sheets arrive as PDFs and as readable designed images, so extraction
carries OCR-grade uncertainty on money values. A wrong parsed rate that reaches a
client is a commercial incident, not a bug — so extraction *proposes* and a person
approves (design doc §5). Nothing here writes an ``accommodation_rates`` row on
its own.

The source file stays attached, so any stored rate is traceable back to the
document it came from.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin

#: Lifecycle of an uploaded document.
DOCUMENT_STATUSES = ("uploaded", "extracting", "extracted", "reviewed", "failed")

#: Lifecycle of one proposed rate row within a document.
EXTRACTION_STATUSES = ("pending", "confirmed", "rejected")


class SupplierDocument(UUIDPKMixin, TimestampMixin, Base):
    """An uploaded rate sheet, kept as evidence for every rate derived from it."""

    __tablename__ = "supplier_documents"

    #: Both nullable: a document can arrive before the property exists in the
    #: catalogue, and gets attached once someone identifies it.
    accommodation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accommodations.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="uploaded", index=True, nullable=False
    )
    #: Free-text failure detail when extraction could not read the document.
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    extractions: Mapped[list[SupplierDocumentExtraction]] = relationship(
        "SupplierDocumentExtraction",
        back_populates="document",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="SupplierDocumentExtraction.row_index",
    )


class SupplierDocumentExtraction(UUIDPKMixin, TimestampMixin, Base):
    """One candidate rate row parsed out of a document, awaiting confirmation.

    ``proposed`` holds the parsed fields as JSONB rather than as typed columns:
    an extraction is a *claim about* a rate, which may be partial or malformed,
    and forcing it into the rate schema before a human reads it would lose the
    detail needed to correct it. On confirmation a real ``AccommodationRate`` is
    written and linked back through ``created_rate_id``.
    """

    __tablename__ = "supplier_document_extractions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("supplier_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Position within the document, so the confirm screen keeps source order.
    row_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, nullable=False
    )
    #: Parsed candidate: season window, room type, meal plan, rate, VAT basis,
    #: rack-vs-STO, any stated discount — plus whatever could not be mapped.
    proposed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: Extractor's own confidence, where it reports one (0..1).
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accommodation_rates.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document: Mapped[SupplierDocument] = relationship(
        "SupplierDocument", back_populates="extractions"
    )
