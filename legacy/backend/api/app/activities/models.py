import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100))

    tours: Mapped[list[Any]] = relationship(
        "TourActivity", back_populates="activity", lazy="selectin"
    )
