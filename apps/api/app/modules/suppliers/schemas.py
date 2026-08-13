from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SupplierBase(BaseModel):
    name: str
    type: str = "other"
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    notes: str | None = None
    is_active: bool = True


class SupplierCreate(SupplierBase):
    slug: str | None = None


class SupplierUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class SupplierRead(SupplierBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    created_at: datetime
