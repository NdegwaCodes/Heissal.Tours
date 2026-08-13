"""User & role-brief schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RoleBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str  # validated strictly on input (UserCreate); permissive on output
    full_name: str | None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    last_login_at: datetime | None
    roles: list[RoleBrief] = []
    permissions: list[str] = []

    @classmethod
    def from_user(cls, user) -> UserRead:  # noqa: ANN001
        data = cls.model_validate(user)
        data.permissions = sorted(user.permission_keys)
        return data


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    role_keys: list[str] = []


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None


class AssignRolesRequest(BaseModel):
    role_keys: list[str]
