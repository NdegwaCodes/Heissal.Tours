"""RBAC schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    description: str | None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    is_system: bool
    created_at: datetime
    permissions: list[PermissionRead] = []


class RoleCreate(BaseModel):
    key: str
    name: str
    description: str | None = None
    permission_keys: list[str] = []


class SetRolePermissionsRequest(BaseModel):
    permission_keys: list[str]
