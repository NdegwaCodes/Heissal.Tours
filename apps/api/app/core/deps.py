"""Shared FastAPI dependencies: current user + permission guard."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import jwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.modules.users.models import User

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    auto_error=False,
)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise AuthenticationError("Not authenticated.")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Could not validate credentials.") from exc

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type.")

    subject = payload.get("sub")
    if not subject:
        raise AuthenticationError("Could not validate credentials.")

    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise AuthenticationError("Could not validate credentials.") from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("Could not validate credentials.")
    return user


def require_permission(permission: str) -> Callable[..., Awaitable[User]]:
    """Return a dependency that enforces `permission` on the current user."""

    async def _guard(current_user: User = Depends(get_current_user)) -> User:
        keys = current_user.permission_keys
        if "*" in keys or permission in keys:
            return current_user
        raise PermissionDeniedError(f"Missing required permission: {permission}")

    return _guard


async def client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None
