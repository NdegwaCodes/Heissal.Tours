"""Stage 2.5 — vehicles, fuel prices, transport cost."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_vehicle_crud(client, admin_tokens):
    h = await _auth(admin_tokens)
    resp = await client.post(
        f"{API}/vehicles",
        headers=h,
        json={
            "name": f"Land Cruiser {uuid.uuid4().hex[:6]}",
            "passenger_capacity": 6,
            "fuel_type": "diesel",
            "fuel_consumption_kmpl": "7",
            "daily_operating_cost": "20",
            "driver_cost_per_day": "35",
            "currency": "USD",
        },
    )
    assert resp.status_code == 201, resp.text
    v = resp.json()
    assert v["slug"].startswith("land-cruiser-")
    assert v["fuel_consumption_kmpl"] == "7.0000"


async def test_fuel_price_resolution(client, admin_tokens):
    h = await _auth(admin_tokens)
    ft = f"diesel-{uuid.uuid4().hex[:6]}"
    # Two prices; the later effective_from should win for a date after it.
    await client.post(
        f"{API}/fuel-prices",
        headers=h,
        json={"fuel_type": ft, "price_per_litre": "1.20", "currency": "USD",
              "effective_from": "2026-01-01"},
    )
    await client.post(
        f"{API}/fuel-prices",
        headers=h,
        json={"fuel_type": ft, "price_per_litre": "1.35", "currency": "USD",
              "effective_from": "2026-06-01"},
    )
    r = await client.get(
        f"{API}/fuel-prices/resolve", headers=h,
        params={"fuel_type": ft, "on_date": "2026-07-01"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["price_per_litre"] == "1.3500"

    # Before any price -> 404
    r = await client.get(
        f"{API}/fuel-prices/resolve", headers=h,
        params={"fuel_type": ft, "on_date": "2025-01-01"},
    )
    assert r.status_code == 404


async def test_vehicle_rbac(client, admin_tokens):
    admin_h = await _auth(admin_tokens)
    email = unique_email("veh-viewer")
    await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    r = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"{API}/vehicles", headers=viewer_h)).status_code == 200
    resp = await client.post(
        f"{API}/vehicles", headers=viewer_h,
        json={"name": "No", "fuel_consumption_kmpl": "7", "currency": "USD"},
    )
    assert resp.status_code == 403
