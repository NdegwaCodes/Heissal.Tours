"""Pytest fixtures — async HTTP client, admin auth, and the seeded catalogue.

The ``sample_catalogue`` fixture is session-scoped on purpose. Building a
scenario through the API costs about a dozen round trips, and against a hosted
database that is ~100 seconds; doing it per test made a ten-test file take
seventeen minutes. Seeding once per session and asserting against known ids is
both faster and better coverage, because the seeded set deliberately contains
cases nobody rebuilds by hand every time (an STO rate beside a discounted rack
rate, a bed-and-breakfast-only property, a 4-guest villa, non-resident pricing,
activity price tiers, rail fares, per-vehicle transfer prices).

Tests that MUTATE catalogue data — adding a rate to watch a price move, creating
an overlapping season — must not use this fixture. They build their own throwaway
property, so a shared read-mostly dataset cannot be corrupted by execution order.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.db.seed_demo import seed_demo
from app.db.session import AsyncSessionLocal
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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def sample_catalogue() -> dict[str, Any]:
    """Seed the demo catalogue once per session and return its ids.

    Idempotent, so a re-run against a database that already holds it is a handful
    of SELECTs rather than a rebuild.
    """
    async with AsyncSessionLocal() as db:
        return await seed_demo(db)


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}+{uuid.uuid4().hex[:10]}@heissaltest.com"


def auth_headers(tokens: dict[str, str]) -> dict[str, str]:
    """Bearer header from a login response."""
    return {"Authorization": f"Bearer {tokens['access_token']}"}
