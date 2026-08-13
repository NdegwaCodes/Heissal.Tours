"""Auth endpoints: login, refresh, logout, me."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_current_user
from app.core.redis import get_redis
from app.db.session import get_db
from app.modules.auth.schemas import RefreshRequest, TokenPair
from app.modules.auth.service import AuthService
from app.modules.users.models import User
from app.modules.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenPair:
    service = AuthService(db, redis)
    user = await service.authenticate(form.username, form.password)
    tokens = await service.issue_tokens(
        user, user_agent=request.headers.get("user-agent"), ip=await client_ip(request)
    )
    await db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenPair:
    service = AuthService(db, redis)
    tokens = await service.refresh(
        body.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=await client_ip(request),
    )
    await db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    service = AuthService(db, redis)
    await service.revoke(body.refresh_token)
    await db.commit()


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.from_user(current_user)
