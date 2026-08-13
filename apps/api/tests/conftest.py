"""Pytest fixtures — async HTTP client bound to the ASGI app."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def admin_tokens(client: AsyncClient) -> dict[str, str]:
    resp = await client.post(
        f"{settings.API_V1_STR}/auth/login",
        data={
            "username": settings.FIRST_SUPERUSER_EMAIL,
            "password": settings.FIRST_SUPERUSER_PASSWORD,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}+{uuid.uuid4().hex[:10]}@heissaltest.com"
