"""Stage 2.7 — clients + quote domain (assembly, numbering, RBAC)."""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _residence_category(client, h, key="non_resident"):
    cats = (await client.get(f"{API}/residence-categories", headers=h)).json()
    return next(c for c in cats if c["key"] == key)


async def _make_client(client, h, **over):
    payload = {"name": f"Client {uuid.uuid4().hex[:8]}", "email": unique_email("client")}
    payload.update(over)
    r = await client.post(f"{API}/clients", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _make_destination(client, h):
    r = await client.post(
        f"{API}/destinations",
        headers=h,
        json={"name": f"Dest {uuid.uuid4().hex[:8]}", "type": "park"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- Clients ----------------------------------------------------------------

async def test_client_crud(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    created = await _make_client(client, h, residence_category_id=rc["id"], country="US")
    assert created["is_active"] is True
    assert created["residence_category_id"] == rc["id"]

    got = await client.get(f"{API}/clients/{created['id']}", headers=h)
    assert got.status_code == 200
    assert got.json()["country"] == "US"

    upd = await client.patch(
        f"{API}/clients/{created['id']}", headers=h, json={"phone": "+254700000000"}
    )
    assert upd.status_code == 200
    assert upd.json()["phone"] == "+254700000000"

    listing = await client.get(f"{API}/clients", headers=h)
    assert listing.status_code == 200
    assert any(c["id"] == created["id"] for c in listing.json())


async def test_clients_require_auth(client):
    r = await client.get(f"{API}/clients")
    assert r.status_code == 401


# --- Quote assembly ---------------------------------------------------------

async def test_quote_minimal_assembly_and_number(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)  # non_resident -> default USD
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    dest = await _make_destination(client, h)

    r = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": cl["id"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-05",
            "travellers": [
                {"traveller_type": "adult"},
                {"traveller_type": "child", "age": 8},
            ],
            "legs": [{"destination_id": dest["id"], "nights": 4}],
        },
    )
    assert r.status_code == 201, r.text
    q = r.json()
    # Number format HTQ-<year>-NNNN; currency defaulted from residence category.
    assert re.fullmatch(rf"HTQ-{date.today().year}-\d{{4,}}", q["quote_number"]), q["quote_number"]
    assert q["presentation_currency"] == "USD"
    assert q["status"] == "draft"
    assert q["current_version_id"] is None
    assert len(q["travellers"]) == 2
    assert len(q["legs"]) == 1
    assert q["legs"][0]["sequence"] == 1
    assert q["legs"][0]["nights"] == 4

    # Read it back with the same nested shape.
    got = await client.get(f"{API}/quotes/{q['id']}", headers=h)
    assert got.status_code == 200
    assert got.json()["quote_number"] == q["quote_number"]


async def test_quote_number_increments(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    dest = await _make_destination(client, h)
    body = {
        "client_id": cl["id"],
        "arrival_date": "2026-08-01",
        "departure_date": "2026-08-03",
        "legs": [{"destination_id": dest["id"], "nights": 2}],
    }
    q1 = (await client.post(f"{API}/quotes", headers=h, json=body)).json()
    q2 = (await client.post(f"{API}/quotes", headers=h, json=body)).json()
    n1 = int(q1["quote_number"].rsplit("-", 1)[1])
    n2 = int(q2["quote_number"].rsplit("-", 1)[1])
    assert n2 == n1 + 1


async def test_quote_full_assembly_with_selections(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    dest = await _make_destination(client, h)

    acc = (
        await client.post(
            f"{API}/accommodations",
            headers=h,
            json={"name": f"Camp {uuid.uuid4().hex[:6]}", "destination_id": dest["id"],
                  "category": "tented_camp"},
        )
    ).json()
    room = (
        await client.post(
            f"{API}/accommodations/{acc['id']}/room-types",
            headers=h,
            json={"name": "Double", "code": "DBL", "max_occupancy": 2},
        )
    ).json()
    meal_plans = (await client.get(f"{API}/meal-plans", headers=h)).json()
    bb = next(m for m in meal_plans if m["code"] == "BB")
    activity = (
        await client.post(
            f"{API}/activities", headers=h,
            json={"name": f"Balloon {uuid.uuid4().hex[:6]}", "duration_minutes": 60},
        )
    ).json()
    vehicle = (
        await client.post(
            f"{API}/vehicles", headers=h,
            json={"name": f"Cruiser {uuid.uuid4().hex[:6]}", "passenger_capacity": 6,
                  "fuel_type": "diesel", "fuel_consumption_kmpl": "7",
                  "daily_operating_cost": "20", "driver_cost_per_day": "35", "currency": "USD"},
        )
    ).json()

    r = await client.post(
        f"{API}/quotes",
        headers=h,
        json={
            "client_id": cl["id"],
            "presentation_currency": "USD",
            "residence_category_id": rc["id"],
            "arrival_date": "2026-07-01",
            "departure_date": "2026-07-06",
            "markup_pct": "25",
            "travellers": [{"traveller_type": "adult"}, {"traveller_type": "adult"}],
            "legs": [
                {
                    "destination_id": dest["id"],
                    "nights": 5,
                    "accommodations": [
                        {"accommodation_id": acc["id"], "room_type_id": room["id"],
                         "meal_plan_id": bb["id"], "rooms": 1, "nights": 5}
                    ],
                    "activities": [
                        {"activity_id": activity["id"], "adults": 2, "children": 0}
                    ],
                }
            ],
            "transport": [
                {"vehicle_id": vehicle["id"], "estimated_km": "450", "days": 5}
            ],
        },
    )
    assert r.status_code == 201, r.text
    q = r.json()
    assert Decimal(q["markup_pct"]) == Decimal("25")
    leg = q["legs"][0]
    assert leg["accommodations"][0]["accommodation_id"] == acc["id"]
    assert leg["accommodations"][0]["nights"] == 5
    assert leg["activities"][0]["activity_id"] == activity["id"]
    assert q["transport"][0]["vehicle_id"] == vehicle["id"]
    assert q["transport"][0]["days"] == 5


async def test_quote_status_transition(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    dest = await _make_destination(client, h)
    q = (
        await client.post(
            f"{API}/quotes", headers=h,
            json={"client_id": cl["id"], "arrival_date": "2026-09-01",
                  "departure_date": "2026-09-04",
                  "legs": [{"destination_id": dest["id"], "nights": 3}]},
        )
    ).json()
    r = await client.patch(f"{API}/quotes/{q['id']}/status", headers=h, json={"status": "sent"})
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    bad = await client.patch(f"{API}/quotes/{q['id']}/status", headers=h, json={"status": "nope"})
    assert bad.status_code == 422


# --- Validation / errors ----------------------------------------------------

async def test_quote_rejects_bad_dates(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    dest = await _make_destination(client, h)
    r = await client.post(
        f"{API}/quotes", headers=h,
        json={"client_id": cl["id"], "arrival_date": "2026-07-05",
              "departure_date": "2026-07-05",
              "legs": [{"destination_id": dest["id"], "nights": 1}]},
    )
    assert r.status_code == 422  # departure must be after arrival


async def test_quote_unknown_client_is_404(client, admin_tokens):
    h = _h(admin_tokens)
    r = await client.post(
        f"{API}/quotes", headers=h,
        json={"client_id": str(uuid.uuid4()), "arrival_date": "2026-07-01",
              "departure_date": "2026-07-03", "legs": []},
    )
    assert r.status_code == 404


async def test_quote_bad_destination_reference_is_handled(client, admin_tokens):
    h = _h(admin_tokens)
    rc = await _residence_category(client, h)
    cl = await _make_client(client, h, residence_category_id=rc["id"])
    r = await client.post(
        f"{API}/quotes", headers=h,
        json={"client_id": cl["id"], "arrival_date": "2026-07-01",
              "departure_date": "2026-07-03",
              "legs": [{"destination_id": str(uuid.uuid4()), "nights": 2}]},
    )
    # A dangling FK is surfaced as a clean 4xx, never a 500.
    assert r.status_code == 400, r.text


async def test_quote_missing_currency_when_no_default(client, admin_tokens):
    h = _h(admin_tokens)
    # 'citizen' residence category defaults to KES, so use a client with no
    # category and no explicit currency to trigger the guard.
    cl = await _make_client(client, h)  # no residence_category_id
    dest = await _make_destination(client, h)
    r = await client.post(
        f"{API}/quotes", headers=h,
        json={"client_id": cl["id"], "arrival_date": "2026-07-01",
              "departure_date": "2026-07-03",
              "legs": [{"destination_id": dest["id"], "nights": 2}]},
    )
    # No residence category anywhere -> explicit error, never a guessed default.
    assert r.status_code == 400, r.text


async def test_quote_requires_permission(client, admin_tokens):
    h = _h(admin_tokens)
    # A viewer can read but not create.
    email = unique_email("quote-viewer")
    await client.post(
        f"{API}/users", headers=h,
        json={"email": email, "password": "ViewerPass123", "role_keys": ["viewer"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "ViewerPass123"}
    )
    vh = _h(login.json())
    assert (await client.get(f"{API}/quotes", headers=vh)).status_code == 200
    denied = await client.post(
        f"{API}/quotes", headers=vh,
        json={"client_id": str(uuid.uuid4()), "arrival_date": "2026-07-01",
              "departure_date": "2026-07-03", "legs": []},
    )
    assert denied.status_code == 403
