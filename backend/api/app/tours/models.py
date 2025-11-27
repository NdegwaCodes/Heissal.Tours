import enum
import uuid
from datetime import datetime, date
from typing import Any

from sqlalchemy import (
    String,
    Text,
    Integer,
    Numeric,
    Float,
    Enum as SAEnum,
    DateTime,
    Date,
    ForeignKey,
    Boolean,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base


class TourStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class TourType(Base):
    __tablename__ = "tour_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)

    tours: Mapped[list[Any]] = relationship(
        "Tour", back_populates="tour_type_obj", lazy="selectin"
    )


class AccommodationType(Base):
    __tablename__ = "accommodation_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    tours: Mapped[list[Any]] = relationship(
        "TourAccommodationType", back_populates="accommodation_type", lazy="selectin"
    )


class Tour(Base):
    __tablename__ = "tours"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)

    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL")
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
    )

    tour_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tour_types.id", ondelete="SET NULL")
    )

    base_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    min_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_nights: Mapped[int] = mapped_column(Integer, nullable=False)

    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    average_rating: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[TourStatus] = mapped_column(
        SAEnum(TourStatus), default=TourStatus.draft, index=True
    )

    seo_meta_title: Mapped[str | None] = mapped_column(String(255))
    seo_meta_description: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    location: Mapped[Any] = relationship(
        "Location", back_populates="tours", lazy="joined"
    )
    provider: Mapped[Any | None] = relationship(
        "Provider", back_populates="tours", lazy="selectin"
    )
    tour_type_obj: Mapped[Any | None] = relationship(
        "TourType", back_populates="tours", lazy="selectin"
    )

    highlights: Mapped[list[Any]] = relationship(
        "TourHighlight", back_populates="tour", cascade="all, delete-orphan"
    )
    includes: Mapped[list[Any]] = relationship(
        "TourInclude", back_populates="tour", cascade="all, delete-orphan"
    )
    excludes: Mapped[list[Any]] = relationship(
        "TourExclude", back_populates="tour", cascade="all, delete-orphan"
    )
    departures: Mapped[list[Any]] = relationship(
        "TourDeparture", back_populates="tour", cascade="all, delete-orphan"
    )
    gallery_images: Mapped[list[Any]] = relationship(
        "TourGalleryImage", back_populates="tour", cascade="all, delete-orphan"
    )
    faqs: Mapped[list[Any]] = relationship(
        "TourFaq", back_populates="tour", cascade="all, delete-orphan"
    )
    activities: Mapped[list[Any]] = relationship(
        "TourActivity", back_populates="tour", cascade="all, delete-orphan"
    )
    accommodation_types: Mapped[list[Any]] = relationship(
        "TourAccommodationType", back_populates="tour", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[Any]] = relationship(
        "Review", back_populates="tour", cascade="all, delete-orphan"
    )


class TourHighlight(Base):
    __tablename__ = "tour_highlights"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    tour: Mapped[Any] = relationship(back_populates="highlights")


class TourInclude(Base):
    __tablename__ = "tour_includes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100))

    tour: Mapped[Any] = relationship(back_populates="includes")


class TourExclude(Base):
    __tablename__ = "tour_excludes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100))

    tour: Mapped[Any] = relationship(back_populates="excludes")


class TourDeparture(Base):
    __tablename__ = "tour_departures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )

    departure_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    return_date: Mapped[date] = mapped_column(Date, nullable=False)

    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_slots: Mapped[int] = mapped_column(Integer, nullable=False)

    price_override: Mapped[float | None] = mapped_column(Numeric(10, 2))
    discount_percent: Mapped[int | None] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tour: Mapped[Any] = relationship(back_populates="departures")
    bookings: Mapped[list[Any]] = relationship(
        "Booking", back_populates="departure"
    )


class TourGalleryImage(Base):
    __tablename__ = "tour_gallery_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    tour: Mapped[Any] = relationship(back_populates="gallery_images")


class TourFaq(Base):
    __tablename__ = "tour_faqs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    tour: Mapped[Any] = relationship(back_populates="faqs")


class TourActivity(Base):
    __tablename__ = "tour_activities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )

    tour: Mapped[Any] = relationship(back_populates="activities")
    activity: Mapped[Any] = relationship(back_populates="tours")


class TourAccommodationType(Base):
    __tablename__ = "tour_accommodation_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tour_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tours.id", ondelete="CASCADE"), nullable=False
    )
    accommodation_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accommodation_types.id", ondelete="CASCADE"),
        nullable=False,
    )

    tour: Mapped[Any] = relationship(back_populates="accommodation_types")
    accommodation_type: Mapped[Any] = relationship(
        back_populates="tours"
    )
