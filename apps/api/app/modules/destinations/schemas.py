from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DestinationBase(BaseModel):
    name: str
    type: str = "other"
    country: str = "Kenya"
    region: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    description: str | None = None
    is_active: bool = True


class DestinationCreate(DestinationBase):
    slug: str | None = None


class DestinationUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    country: str | None = None
    region: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    description: str | None = None
    is_active: bool | None = None


class DestinationRead(DestinationBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    created_at: datetime
