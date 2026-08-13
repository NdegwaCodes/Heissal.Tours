"""Stage 2.2 — accommodations, room types, meal plans, and rate selection."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _auth(client, tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _first(client, h, path, match=None):
    resp = await client.get(f"{API}/{path}", headers=h)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    if match:
        items = [i for i in items if match(i)]
    return items[0]


async def test_meal_plans_seeded(client, admin_tokens):
    h = await _auth(client, admin_tokens)
    resp = await client.get(f"{API}/meal-plans", headers=h)
    assert resp.status_code == 200
    codes = {m["code"] for m in resp.json()}
    assert {"RO", "BB", "HB", "FB", "AI"} <= codes


async def test_accommodation_rate_selection(client, admin_tokens):
    h = await _auth(client, admin_tokens)

    # Destination
    dname = f"Rate Park {uuid.uuid4().hex[:8]}"
    dest = (
        await client.post(
            f"{API}/destinations", headers=h, json={"name": dname, "type": "park"}
        )
    ).json()

    # Accommodation
    acc = (
        await client.post(
            f"{API}/accommodations",
            headers=h,
            json={"name": f"Mara Camp {uuid.uuid4().hex[:6]}", "destination_id": dest["id"],
                  "category": "tented_camp", "star_rating": 4},
        )
    ).json()
    assert acc["slug"].startswith("mara-camp-")

    # Room type
    room = (
        await client.post(
            f"{API}/accommodations/{acc['id']}/room-types",
            headers=h,
            json={"name": "Double", "code": "DBL", "max_occupancy": 2},
        )
    ).json()

    bb = await _first(client, h, "meal-plans", lambda m: m["code"] == "BB")
    non_resident = await _first(
        client, h, "residence-categories", lambda c: c["key"] == "non_resident"
    )

    # High-season rate: 1 Jun – 30 Sep 2026, USD 500/night
    rate_resp = await client.post(
        f"{API}/accommodations/{acc['id']}/rates",
        headers=h,
        json={
            "room_type_id": room["id"],
            "meal_plan_id": bb["id"],
            "residence_category_id": non_resident["id"],
            "season_name": "High",
            "effective_from": "2026-06-01",
            "effective_to": "2026-09-30",
            "currency": "USD",
            "rate_per_night": "500.00",
            "child_rate": "250.00",
        },
    )
    assert rate_resp.status_code == 201, rate_resp.text

    # Resolve within the season -> the 500 rate
    resolved = await client.get(
        f"{API}/accommodations/{acc['id']}/resolve-rate",
        headers=h,
        params={
            "room_type_id": room["id"],
            "meal_plan_id": bb["id"],
            "residence_category_id": non_resident["id"],
            "stay_date": "2026-07-15",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["rate_per_night"] == "500.0000"
    assert resolved.json()["season_name"] == "High"

    # Resolve outside any season -> explicit 404 (never assume a price)
    miss = await client.get(
        f"{API}/accommodations/{acc['id']}/resolve-rate",
        headers=h,
        params={
            "room_type_id": room["id"],
            "meal_plan_id": bb["id"],
            "residence_category_id": non_resident["id"],
            "stay_date": "2026-12-25",
        },
    )
    assert miss.status_code == 404
    assert miss.json()["error"]["code"] == "NOT_FOUND"


async def test_rate_bad_date_range_rejected(client, admin_tokens):
    h = await _auth(client, admin_tokens)
    dest = (
        await client.post(
            f"{API}/destinations",
            headers=h,
            json={"name": f"BadRange {uuid.uuid4().hex[:8]}", "type": "park"},
        )
    ).json()
    acc = (
        await client.post(
            f"{API}/accommodations",
            headers=h,
            json={"name": f"Camp {uuid.uuid4().hex[:6]}", "destination_id": dest["id"]},
        )
    ).json()
    room = (
        await client.post(
            f"{API}/accommodations/{acc['id']}/room-types",
            headers=h,
            json={"name": "Single", "max_occupancy": 1},
        )
    ).json()
    bb = await _first(client, h, "meal-plans", lambda m: m["code"] == "BB")
    cat = await _first(client, h, "residence-categories", lambda c: c["key"] == "citizen")

    resp = await client.post(
        f"{API}/accommodations/{acc['id']}/rates",
        headers=h,
        json={
            "room_type_id": room["id"],
            "meal_plan_id": bb["id"],
            "residence_category_id": cat["id"],
            "effective_from": "2026-09-30",
            "effective_to": "2026-06-01",  # inverted
            "currency": "KES",
            "rate_per_night": "15000",
        },
    )
    assert resp.status_code == 400


async def test_accommodation_rbac(client, admin_tokens):
    admin_h = await _auth(client, admin_tokens)
    email = unique_email("acc-viewer")
    await client.post(
        f"{API}/users",
        headers=admin_h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    r = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    viewer_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # read ok
    assert (await client.get(f"{API}/accommodations", headers=viewer_h)).status_code == 200
    # manage forbidden
    resp = await client.post(
        f"{API}/accommodations",
        headers=viewer_h,
        json={"name": "Nope", "destination_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403
