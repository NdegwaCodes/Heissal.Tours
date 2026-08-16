from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class ClientBase(BaseModel):
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    country: str | None = None
    nationality: str | None = None
    residence_category_id: uuid.UUID | None = None
    notes: str | None = None
    is_active: bool = True


class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    country: str | None = None
    nationality: str | None = None
    residence_category_id: uuid.UUID | None = None
    notes: str | None = None
    is_active: bool | None = None


class ClientRead(ClientBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
