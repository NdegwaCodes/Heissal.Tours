"""Stage 2.8 — PricingEngine: /quotes/calculate, persistence/versioning, views.

The scenario is priced by hand so the assertions pin the engine's arithmetic:

  accommodation  500/night x 1 room x 3 nights           = 1500
  activity       450 x 2 adults + 250 x 1 child           = 1150
  park fee       (70 x 2 + 40 x 1 + 0) x 3 days           =  540
  transport fuel 210 km / 7 kmpl = 30 L x 1.50            =   45
  transport svc  (35 driver + 20 operating) x 3 days      =  165
  ----------------------------------------------------------------
  internal_cost                                            = 3400
  + 25% markup -> selling 4250 ; profit 850 ; margin 0.20
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from tests.conftest import unique_email

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

SEASON = {"effective_from": "2026-06-01", "effective_to": "2026-09-30"}
ARRIVAL, DEPARTURE, CHECK_IN = "2026-07-01", "2026-07-08", "2026-07-01"


def _h(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _residence(client, h, key="non_resident"):
    cats = (await client.get(f"{API}/residence-categories", headers=h)).json()
    return next(c for c in cats if c["key"] == key)


async def _setup(client, h):
    """Create a full priced scenario; return the ids the engine needs."""
    rc = await _residence(client, h)
    dest = (
        await client.post(
            f"{API}/destinations", headers=h,
            json={"name": f"Mara {uuid.uuid4().hex[:8]}", "type": "park"},
        )
    ).json()
    acc = (
        await client.post(
            f"{API}/accommodations", headers=h,
            json={"name": f"Camp {uuid.uuid4().hex[:6]}", "destination_id": dest["id"],
                  "category": "tented_camp"},
        )
    ).json()
    room = (
        await client.post(
            f"{API}/accommodations/{acc['id']}/room-types", headers=h,
            json={"name": "Double", "code": "DBL", "max_occupancy": 2},
        )
    ).json()
    bb = next(
        m for m in (await client.get(f"{API}/meal-plans", headers=h)).json() if m["code"] == "BB"
    )
    await client.post(
        f"{API}/accommodations/{acc['id']}/rates", headers=h,
        json={"room_type_id": room["id"], "meal_plan_id": bb["id"],
              "residence_category_id": rc["id"], "season_name": "High",
              "currency": "USD", "rate_per_night": "500", "child_rate": "250", **SEASON},
    )
    await client.post(
        f"{API}/destinations/{dest['id']}/park-fees", headers=h,
        json={"residence_category_id": rc["id"], "currency": "USD",
              "adult": "70", "child": "40", "infant": "0",
              "child_min_age": 3, "child_max_age": 11, **SEASON},
    )
    activity = (
        await client.post(
            f"{API}/activities", headers=h,
            json={"name": f"Balloon {uuid.uuid4().hex[:6]}", "duration_minutes": 60},
        )
    ).json()
    await client.post(
        f"{API}/activities/{activity['id']}/rates", headers=h,
        json={"residence_category_id": rc["id"], "currency": "USD",
              "adult_price": "450", "child_price": "250", **SEASON},
    )
    fuel_type = f"diesel-{uuid.uuid4().hex[:6]}"
    vehicle = (
        await client.post(
            f"{API}/vehicles", headers=h,
            json={"name": f"Cruiser {uuid.uuid4().hex[:6]}", "passenger_capacity": 6,
                  "fuel_type": fuel_type, "fuel_consumption_kmpl": "7",
                  "daily_operating_cost": "20", "driver_cost_per_day": "35", "currency": "USD"},
        )
    ).json()
    await client.post(
        f"{API}/fuel-prices", headers=h,
        json={"fuel_type": fuel_type, "price_per_litre": "1.5", "currency": "USD",
              "effective_from": "2026-01-01"},
    )
    return {
        "rc": rc["id"], "dest": dest["id"], "acc": acc["id"], "room": room["id"],
        "bb": bb["id"], "activity": activity["id"], "vehicle": vehicle["id"],
    }


def _request(ids, currency="USD", markup="25"):
    return {
        "residence_category_id": ids["rc"],
        "presentation_currency": currency,
        "arrival_date": ARRIVAL,
        "departure_date": DEPARTURE,
        "markup_pct": markup,
        "travellers": [
            {"traveller_type": "adult"}, {"traveller_type": "adult"},
            {"traveller_type": "child", "age": 8},
        ],
        "legs": [{
            "destination_id": ids["dest"], "nights": 3, "check_in": CHECK_IN,
            "accommodations": [{"accommodation_id": ids["acc"], "room_type_id": ids["room"],
                                "meal_plan_id": ids["bb"], "rooms": 1, "nights": 3}],
            "activities": [{"activity_id": ids["activity"], "adults": 2, "children": 1}],
        }],
        "transport": [{"vehicle_id": ids["vehicle"], "estimated_km": "210", "days": 3}],
    }


async def test_calculate_full_breakdown(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    r = await client.post(f"{API}/quotes/calculate", headers=h, json=_request(ids))
    assert r.status_code == 200, r.text
    res = r.json()
    assert Decimal(res["internal_cost"]) == Decimal("3400")
    assert Decimal(res["selling_subtotal"]) == Decimal("4250")
    assert Decimal(res["selling_price"]) == Decimal("4250")
    assert Decimal(res["gross_profit"]) == Decimal("850")
    assert Decimal(res["gross_margin"]) == Decimal("0.2")
    # Five lines: accommodation, park_fee, activity, transport_fuel, transport_service.
    cats = [ln["category"] for ln in res["lines"]]
    assert cats == ["accommodation", "park_fee", "activity", "transport_fuel", "transport_service"]
    # Client price per line sums to the selling subtotal.
    assert sum(Decimal(ln["client_price"]) for ln in res["lines"]) == Decimal("4250")
    # Invariant: profit = selling - internal.
    assert Decimal(res["gross_profit"]) == Decimal(res["selling_price"]) - Decimal(
        res["internal_cost"]
    )


async def test_calculate_converts_to_presentation_currency(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    # 1 USD = 130 KES on/after the quote date.
    await client.post(
        f"{API}/exchange-rates", headers=h,
        json={"base_currency": "USD", "quote_currency": "KES", "rate": "130",
              "effective_from": "2026-01-01"},
    )
    r = await client.post(f"{API}/quotes/calculate", headers=h, json=_request(ids, currency="KES"))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["presentation_currency"] == "KES"
    assert Decimal(res["internal_cost"]) == Decimal("442000")  # 3400 * 130
    assert Decimal(res["selling_price"]) == Decimal("552500")  # 442000 * 1.25


async def test_calculate_missing_rate_is_404(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    req = _request(ids)
    req["arrival_date"] = "2027-01-01"   # outside every seasonal rate window
    req["departure_date"] = "2027-01-05"
    req["legs"][0]["check_in"] = "2027-01-01"
    r = await client.post(f"{API}/quotes/calculate", headers=h, json=req)
    assert r.status_code == 404, r.text  # never a guessed price


async def test_price_persists_immutable_versions(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    cl = (
        await client.post(
            f"{API}/clients", headers=h,
            json={"name": f"Client {uuid.uuid4().hex[:8]}", "residence_category_id": ids["rc"]},
        )
    ).json()
    quote = (
        await client.post(
            f"{API}/quotes", headers=h,
            json={"client_id": cl["id"], "presentation_currency": "USD",
                  "residence_category_id": ids["rc"], "arrival_date": ARRIVAL,
                  "departure_date": DEPARTURE, "markup_pct": "25",
                  "travellers": [{"traveller_type": "adult"}, {"traveller_type": "adult"},
                                 {"traveller_type": "child", "age": 8}],
                  "legs": [{"destination_id": ids["dest"], "nights": 3, "check_in": CHECK_IN,
                            "accommodations": [{"accommodation_id": ids["acc"],
                                                "room_type_id": ids["room"],
                                                "meal_plan_id": ids["bb"],
                                                "rooms": 1, "nights": 3}],
                            "activities": [{"activity_id": ids["activity"], "adults": 2,
                                            "children": 1}]}],
                  "transport": [{"vehicle_id": ids["vehicle"], "estimated_km": "210", "days": 3}]},
        )
    ).json()

    v1 = await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)
    assert v1.status_code == 200, v1.text
    body = v1.json()
    assert body["version_number"] == 1
    assert Decimal(body["internal_cost"]) == Decimal("3400.00")
    assert Decimal(body["selling_price"]) == Decimal("4250.00")
    assert len(body["items"]) == 5

    # Re-pricing appends V2, never mutates V1.
    v2 = await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)
    assert v2.json()["version_number"] == 2

    versions = (await client.get(f"{API}/quotes/{quote['id']}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions] == [2, 1]

    # The quote now points at its latest version.
    got = (await client.get(f"{API}/quotes/{quote['id']}", headers=h)).json()
    assert got["current_version_id"] == v2.json()["id"]


async def test_client_view_hides_cost(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    # A sales agent has quote:create but not quote:read_cost.
    email = unique_email("agent")
    await client.post(
        f"{API}/users", headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["sales_agent"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    ah = _h(login.json())
    r = await client.post(f"{API}/quotes/calculate", headers=ah, json=_request(ids))
    assert r.status_code == 200, r.text
    res = r.json()
    # Client view exposes the price, never cost or margin.
    assert Decimal(res["selling_price"]) == Decimal("4250")
    assert "internal_cost" not in res
    assert "gross_margin" not in res
    for ln in res["lines"]:
        assert "client_price" in ln
        assert "internal_cost" not in ln
