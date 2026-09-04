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
from datetime import date
from decimal import Decimal
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.seed_demo import seed_demo
from app.db.session import AsyncSessionLocal
from app.main import app
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    MealPlan,
    RoomType,
)
from app.modules.destinations.models import Destination
from app.modules.quotes.models import Quote, QuoteOption, QuoteOptionLeg
from app.modules.residence.models import ResidenceCategory


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


# The lodge's own rates, invented like the rest of the demo data. Held here
# because the fixture is here; the tests that assert on them restate them.
D = Decimal
SEASON_FROM, SEASON_TO = date(2026, 1, 1), date(2026, 12, 31)
LODGE_TWIN = D("18000")


# --------------------------------------------------------------------------- #
# A second destination
# --------------------------------------------------------------------------- #
#
# The demo catalogue is entirely in Diani, so anything that needs a genuine
# two-destination trip — a package (§3.9), a document that prints its legs
# (§3.11) — needs a place of its own. Here rather than in one test module
# because two now use it, and a fixture imported across test files shadows
# itself the moment a test names it as a parameter.

@pytest_asyncio.fixture(loop_scope="session")
async def upcountry_lodge():
    """A lodge in its own destination, so a package can have two real legs."""
    tag = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        fb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "FB"))
        ).scalar_one()
        bb = (
            await db.execute(select(MealPlan).where(MealPlan.code == "BB"))
        ).scalar_one()
        citizen = (
            await db.execute(
                select(ResidenceCategory).where(ResidenceCategory.key == "citizen")
            )
        ).scalar_one()
        where = Destination(
            name=f"Package Highlands {tag}", slug=f"package-highlands-{tag}", type="park"
        )
        db.add(where)
        await db.flush()
        lodge = Accommodation(
            name=f"Highland Lodge {tag}",
            slug=f"highland-lodge-{tag}",
            destination_id=where.id,
            category="lodge",
        )
        db.add(lodge)
        await db.flush()
        room = RoomType(
            accommodation_id=lodge.id, name="Hill Twin", code="HTW", max_occupancy=2
        )
        db.add(room)
        await db.flush()
        for plan, twin in ((fb, LODGE_TWIN), (bb, LODGE_TWIN - D("4000"))):
            for occupancy, amount in ((2, twin), (1, twin * D("0.7"))):
                db.add(
                    AccommodationRate(
                        accommodation_id=lodge.id,
                        room_type_id=room.id,
                        meal_plan_id=plan.id,
                        residence_category_id=citizen.id,
                        season_name="standard",
                        occupancy=occupancy,
                        effective_from=SEASON_FROM,
                        effective_to=SEASON_TO,
                        currency="KES",
                        rate_per_night=amount,
                        rate_kind="sto",
                    )
                )
        # A property with NO rates, so a leg pointing at it genuinely cannot be
        # priced. Chui Festive Camp will not do: its four-night minimum binds
        # only in the festive season, so a July leg prices happily.
        bare = Accommodation(
            name=f"Rateless Camp {tag}",
            slug=f"rateless-camp-{tag}",
            destination_id=where.id,
            category="camp",
        )
        db.add(bare)
        await db.flush()
        await db.commit()
        ids = {
            "destination_id": str(where.id),
            "accommodation_id": str(lodge.id),
            "rateless_id": str(bare.id),
            "meal_plan_fb": str(fb.id),
            "meal_plan_bb": str(bb.id),
        }

    yield ids

    async with AsyncSessionLocal() as db:
        # A package's option row points at its FIRST property, so the lodge is
        # reachable only through the leg rows. Matching on the option alone left
        # quotes behind that held a reference to the room type.
        mine = [uuid.UUID(ids["accommodation_id"]), uuid.UUID(ids["rateless_id"])]
        via_option = (
            await db.execute(
                select(QuoteOption.quote_id).where(
                    QuoteOption.accommodation_id.in_(mine)
                )
            )
        ).scalars().all()
        via_leg = (
            await db.execute(
                select(QuoteOption.quote_id)
                .join(QuoteOptionLeg, QuoteOptionLeg.quote_option_id == QuoteOption.id)
                .where(QuoteOptionLeg.accommodation_id.in_(mine))
            )
        ).scalars().all()
        for quote_id in set(via_option) | set(via_leg):
            row = await db.get(Quote, quote_id)
            if row is not None:
                await db.delete(row)
        await db.flush()
        for key in ("accommodation_id", "rateless_id"):
            row = await db.get(Accommodation, uuid.UUID(ids[key]))
            if row is not None:
                await db.delete(row)
        await db.flush()
        where_row = await db.get(Destination, uuid.UUID(ids["destination_id"]))
        if where_row is not None:
            await db.delete(where_row)
        await db.commit()
