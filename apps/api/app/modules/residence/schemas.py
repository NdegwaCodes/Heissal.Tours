from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResidenceCategoryBase(BaseModel):
    name: str
    description: str | None = None
    sort_order: int = 0
    default_currency_code: str | None = None
    is_active: bool = True


class ResidenceCategoryCreate(ResidenceCategoryBase):
    key: str


class ResidenceCategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    default_currency_code: str | None = None
    is_active: bool | None = None


class ResidenceCategoryRead(ResidenceCategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    created_at: datetime
