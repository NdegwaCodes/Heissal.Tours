"""Password hashing (Argon2id) and JWT access-token helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher

from app.core.config import settings

_hasher = PasswordHasher()


# --- Passwords ---

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return _hasher.verify(hashed_password, plain_password)
    except Exception:
        return False


def needs_rehash(hashed_password: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed_password)
    except Exception:
        return False


# --- Access tokens (JWT) ---

def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(tz=UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode & verify a JWT. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )


# --- Refresh tokens (opaque, stored hashed) ---

def generate_refresh_token() -> str:
    """A high-entropy opaque token string (not a JWT)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Deterministic hash for storage/lookup of refresh tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(tz=UTC) + timedelta(
        minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES
    )


def new_jti() -> str:
    return str(uuid.uuid4())
