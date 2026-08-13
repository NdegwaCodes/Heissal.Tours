"""Authentication service: login, token issuance, refresh rotation, revocation.

Refresh tokens are opaque, stored only as SHA-256 hashes, and rotated on use:
redeeming a refresh token revokes it and issues a new pair. Revoked/expired
tokens are rejected. A Redis deny-list gives O(1) revocation checks in addition
to the DB record.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_expiry,
    verify_password,
)
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas import TokenPair
from app.modules.users.models import User

_DENY_PREFIX = "revoked_refresh:"


class AuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    async def authenticate(self, email: str, password: str) -> User:
        user = (
            await self.db.execute(select(User).where(User.email == email.lower().strip()))
        ).scalar_one_or_none()
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Incorrect email or password.")
        if not user.is_active:
            raise AuthenticationError("Account is disabled.")
        return user

    async def issue_tokens(
        self, user: User, *, user_agent: str | None = None, ip: str | None = None
    ) -> TokenPair:
        access = create_access_token(subject=str(user.id))
        raw_refresh = generate_refresh_token()
        record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=refresh_expiry(),
            user_agent=user_agent,
            ip=ip,
        )
        self.db.add(record)
        user.last_login_at = datetime.now(tz=UTC)
        await self.db.flush()
        return TokenPair(access_token=access, refresh_token=raw_refresh)

    async def refresh(
        self, raw_refresh: str, *, user_agent: str | None = None, ip: str | None = None
    ) -> TokenPair:
        token_hash = hash_refresh_token(raw_refresh)
        if await self.redis.get(_DENY_PREFIX + token_hash):
            raise AuthenticationError("Refresh token has been revoked.")

        record = (
            await self.db.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()

        if record is None or record.revoked_at is not None:
            raise AuthenticationError("Invalid refresh token.")
        if record.expires_at < datetime.now(tz=UTC):
            raise AuthenticationError("Refresh token has expired.")

        # Rotate: revoke the old, issue a new pair.
        await self._revoke_record(record)
        user = (
            await self.db.execute(select(User).where(User.id == record.user_id))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token.")
        return await self.issue_tokens(user, user_agent=user_agent, ip=ip)

    async def revoke(self, raw_refresh: str) -> None:
        token_hash = hash_refresh_token(raw_refresh)
        record = (
            await self.db.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if record is not None and record.revoked_at is None:
            await self._revoke_record(record)

    async def _revoke_record(self, record: RefreshToken) -> None:
        record.revoked_at = datetime.now(tz=UTC)
        await self.db.flush()
        # Mirror into Redis deny-list with a TTL until natural expiry.
        ttl = int((record.expires_at - datetime.now(tz=UTC)).total_seconds())
        if ttl > 0:
            await self.redis.set(_DENY_PREFIX + record.token_hash, "1", ex=ttl)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        records = (
            await self.db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        for record in records:
            await self._revoke_record(record)
        return len(records)
