"""Image metadata for properties and destinations (Stage 3.1).

Only metadata lives here — the bytes sit on disk / object storage and are served
through the API so access stays permission-checked rather than resting on a
guessable public path (design doc §6). Originals are kept; the quotation
template's aspect ratios come from centre-cropped derivatives, so a layout change
can re-derive them without re-collecting photographs.

Two near-identical tables rather than one polymorphic ``media_assets`` table: a
real foreign key per owner is enforceable by the database, an ``owner_type`` +
``owner_id`` pair is not.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, UUIDPKMixin


class _ImageColumns:
    """Columns shared by every stored image."""

    #: Path within the configured media root — never a full public URL, so the
    #: storage backend can change without rewriting rows.
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    #: SHA-256 of the original bytes — dedupes re-uploads of the same photo.
    checksum: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PropertyImage(UUIDPKMixin, TimestampMixin, _ImageColumns, Base):
    """One image belonging to an accommodation (5–6 per property in practice)."""

    __tablename__ = "property_images"
    __table_args__ = (
        # The same photo cannot be attached to one property twice.
        UniqueConstraint("accommodation_id", "checksum", name="uq_property_image_checksum"),
    )

    accommodation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accommodations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: The large image at the top of a property page; the rest are thumbnails.
    is_hero: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DestinationImage(UUIDPKMixin, TimestampMixin, _ImageColumns, Base):
    """Imagery for a destination, including the quotation cover.

    The cover is a destination asset, not a per-quote choice: every Diani
    proposal opens on the same coastal cover (design doc §3.11).
    """

    __tablename__ = "destination_images"
    __table_args__ = (
        UniqueConstraint("destination_id", "checksum", name="uq_destination_image_checksum"),
    )

    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Marks the document cover image for this destination.
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
