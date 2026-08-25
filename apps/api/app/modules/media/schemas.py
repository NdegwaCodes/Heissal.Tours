from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImageRead(BaseModel):
    """Metadata for one stored image. The bytes are fetched from ``url``."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content_type: str
    byte_size: int
    checksum: str
    width: int | None
    height: int | None
    alt_text: str | None
    sort_order: int
    created_at: datetime


class PropertyImageRead(ImageRead):
    accommodation_id: uuid.UUID
    is_hero: bool


class DestinationImageRead(ImageRead):
    destination_id: uuid.UUID
    is_cover: bool
