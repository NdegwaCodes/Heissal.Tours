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


def _refuse_non_test_database() -> None:
    """Refuse to run against a database whose name does not end in ``_test``.

    ``scripts/test_local.sh`` already checks this, but plain ``pytest`` does not
    go through it: it reads ``.env``, which points at the **live catalogue**. The
    suite seeds, mutates and deletes, so the only thing that stopped a direct
    ``pytest`` run from writing to production was the live database happening to
    be behind on a migration, which is luck rather than a safeguard.

    Collection-time, so it fails before a single fixture opens a connection.
    """
    url = settings.DATABASE_URL or ""
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith("_test"):
        raise RuntimeError(
            f"REFUSING to run tests against database {name!r} — the name does "
            "not end in '_test'. This suite seeds, mutates and deletes rows.\n"
            "Run `bash scripts/test_local.sh`, which points DATABASE_URL at a "
            "throwaway database, rather than pytest directly."
        )


_refuse_non_test_database()


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


@pytest_asyncio.fixture(loop_scope="session")
async def restore_pricing_config(
    client: AsyncClient, admin_tokens: dict[str, str]
) -> AsyncGenerator[None, None]:
    """Snapshot the pricing config and put it back afterwards.

    The config is ONE row in ``app_settings``, so a test that PATCHes it changes
    global state for every test that runs later. That is exactly what happened:
    a config round-trip test set ``default_tax_pct`` to 16 and left it there, so
    the engine tests — which assert against the default of 0 — started computing
    4930 where they expected 4250. The failure looked like a pricing bug and was
    a test-isolation bug, and it only surfaced once the whole suite ran in one go.

    Any test that mutates the config must depend on this fixture.
    """
    url = f"{settings.API_V1_STR}/pricing-config"
    headers = auth_headers(admin_tokens)
    before = (await client.get(url, headers=headers)).json()
    try:
        yield
    finally:
        await client.patch(url, headers=headers, json=before)


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}+{uuid.uuid4().hex[:10]}@heissaltest.com"


def auth_headers(tokens: dict[str, str]) -> dict[str, str]:
    """Bearer header from a login response."""
    return {"Authorization": f"Bearer {tokens['access_token']}"}
