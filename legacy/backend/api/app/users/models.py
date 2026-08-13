import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    reviews: Mapped[list[Any]] = relationship(
        "Review",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    bookings: Mapped[list[Any]] = relationship(
        "Booking", back_populates="user", lazy="selectin"
    )
    wishlists: Mapped[list[Any]] = relationship(
        "Wishlist", back_populates="user", lazy="selectin"
    )
