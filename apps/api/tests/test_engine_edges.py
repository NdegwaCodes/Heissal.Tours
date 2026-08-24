"""Stage 2.9 — end-to-end edge cases and invariants for the quote engine.

Where ``test_pricing_engine.py`` pins one worked example, this file pins the
properties that must survive real-world drift:

* a sent quote stays reproducible after rates change (version immutability),
* overlapping rate windows resolve by the documented tie-break,
* cost and margin never reach a client-facing role through *any* endpoint,
* park fees bill correctly on the exact child-age boundaries,
* an empty quote prices to zero instead of crashing,
* a missing rate or vehicle is a 404 — never a silently zeroed line.

The scenario builder from ``test_pricing_engine`` is reused so both files price
the same known-good fixture (internal cost 3400 USD).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import settings
from tests.conftest import unique_email
from tests.test_pricing_engine import (
    ARRIVAL,
    CHECK_IN,
    DEPARTURE,
    SEASON,
    _h,
    _request,
    _setup,
)

API = settings.API_V1_STR
pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

#: Keys that must never appear in a client-facing payload.
COST_KEYS = {"internal_cost", "gross_profit", "gross_margin", "selling_subtotal", "source_currency"}


def _keys_everywhere(payload) -> set[str]:
    """Every dict key anywhere in a nested JSON payload."""
    found: set[str] = set()
    if isinstance(payload, dict):
        found |= set(payload)
        for value in payload.values():
            found |= _keys_everywhere(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_everywhere(item)
    return found


async def _quote_for(client, h, ids, **overrides):
    """Create a saved quote over the standard scenario."""
    cl = (
        await client.post(
            f"{API}/clients",
            headers=h,
            json={"name": f"Client {uuid.uuid4().hex[:8]}", "residence_category_id": ids["rc"]},
        )
    ).json()
    body = {
        "client_id": cl["id"],
        "presentation_currency": "USD",
        "residence_category_id": ids["rc"],
        "arrival_date": ARRIVAL,
        "departure_date": DEPARTURE,
        "markup_pct": "25",
        "travellers": [
            {"traveller_type": "adult"},
            {"traveller_type": "adult"},
            {"traveller_type": "child", "age": 8},
        ],
        "legs": [
            {
                "destination_id": ids["dest"],
                "nights": 3,
                "check_in": CHECK_IN,
                "accommodations": [
                    {
                        "accommodation_id": ids["acc"],
                        "room_type_id": ids["room"],
                        "meal_plan_id": ids["bb"],
                        "rooms": 1,
                        "nights": 3,
                    }
                ],
                "activities": [{"activity_id": ids["activity"], "adults": 2, "children": 1}],
            }
        ],
        "transport": [{"vehicle_id": ids["vehicle"], "estimated_km": "210", "days": 3}],
    }
    body.update(overrides)
    resp = await client.post(f"{API}/quotes", headers=h, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _agent_headers(client, h):
    """A sales agent: quote:create + quote:read, but NOT quote:read_cost."""
    email = unique_email("agent")
    await client.post(
        f"{API}/users",
        headers=h,
        json={"email": email, "password": "AgentPass123", "role_keys": ["sales_agent"]},
    )
    login = await client.post(
        f"{API}/auth/login", data={"username": email, "password": "AgentPass123"}
    )
    assert login.status_code == 200, login.text
    return _h(login.json())


# --------------------------------------------------------------------------- #
# The commercial invariant: a priced version is reproducible forever.
# --------------------------------------------------------------------------- #

async def test_version_is_immutable_when_rates_change_underneath(client, admin_tokens):
    """A quote already sent to a client must not silently re-price itself."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    quote = await _quote_for(client, h, ids)

    v1 = (await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)).json()
    assert Decimal(v1["internal_cost"]) == D("3400.00")

    # The camp raises its rate mid-season (a new row, later effective_from).
    bumped = await client.post(
        f"{API}/accommodations/{ids['acc']}/rates",
        headers=h,
        json={
            "room_type_id": ids["room"],
            "meal_plan_id": ids["bb"],
            "residence_category_id": ids["rc"],
            "season_name": "High (revised)",
            "currency": "USD",
            "rate_per_night": "700",
            "child_rate": "350",
            "effective_from": "2026-06-15",
            "effective_to": SEASON["effective_to"],
        },
    )
    assert bumped.status_code == 201, bumped.text

    v2 = (await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)).json()
    # +200/night x 3 nights = +600 on the accommodation line.
    assert Decimal(v2["internal_cost"]) == D("4000.00")
    assert v2["version_number"] == 2

    # V1 is untouched: same id, same numbers, and its snapshot still reads 3400.
    versions = (await client.get(f"{API}/quotes/{quote['id']}/versions", headers=h)).json()
    v1_now = next(v for v in versions if v["version_number"] == 1)
    assert v1_now["id"] == v1["id"]
    assert Decimal(v1_now["selling_price"]) == Decimal(v1["selling_price"]) == D("4250.00")


async def test_repricing_unchanged_data_is_deterministic(client, admin_tokens):
    """Same inputs, same rates -> byte-identical money on the next version."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    quote = await _quote_for(client, h, ids)
    first = (await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)).json()
    second = (await client.post(f"{API}/quotes/{quote['id']}/price", headers=h)).json()
    assert first["id"] != second["id"]
    for field in ("internal_cost", "selling_price", "gross_profit", "gross_margin"):
        assert Decimal(first[field]) == Decimal(second[field])


async def test_overlapping_rate_windows_resolve_to_latest_effective_from(client, admin_tokens):
    """Overlaps are not DB-prevented, so pin the documented tie-break."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    await client.post(
        f"{API}/accommodations/{ids['acc']}/rates",
        headers=h,
        json={
            "room_type_id": ids["room"],
            "meal_plan_id": ids["bb"],
            "residence_category_id": ids["rc"],
            "season_name": "Overlapping",
            "currency": "USD",
            "rate_per_night": "620",
            "effective_from": "2026-06-15",
            "effective_to": SEASON["effective_to"],
        },
    )
    resolved = await client.get(
        f"{API}/accommodations/{ids['acc']}/resolve-rate",
        headers=h,
        params={
            "room_type_id": ids["room"],
            "meal_plan_id": ids["bb"],
            "residence_category_id": ids["rc"],
            "stay_date": CHECK_IN,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert Decimal(resolved.json()["rate_per_night"]) == D("620.0000")
    assert resolved.json()["season_name"] == "Overlapping"


# --------------------------------------------------------------------------- #
# Cost/margin must not leak through ANY endpoint a client-facing role can call.
# --------------------------------------------------------------------------- #

async def test_no_endpoint_leaks_cost_to_a_client_facing_role(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    quote = await _quote_for(client, h, ids)
    ah = await _agent_headers(client, h)

    calculated = await client.post(f"{API}/quotes/calculate", headers=ah, json=_request(ids))
    priced = await client.post(f"{API}/quotes/{quote['id']}/price", headers=ah)
    versions = await client.get(f"{API}/quotes/{quote['id']}/versions", headers=ah)

    for label, resp in (
        ("calculate", calculated),
        ("price", priced),
        ("versions", versions),
    ):
        assert resp.status_code == 200, f"{label}: {resp.text}"
        leaked = _keys_everywhere(resp.json()) & COST_KEYS
        assert not leaked, f"{label} leaked {leaked} to a role without quote:read_cost"

    # The agent still sees a usable selling price.
    assert Decimal(calculated.json()["selling_price"]) == D("4250")
    assert Decimal(priced.json()["selling_price"]) == D("4250.00")


async def test_cost_reader_does_see_cost(client, admin_tokens):
    """The mirror of the leak test: the split must not hide cost from staff."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    res = (await client.post(f"{API}/quotes/calculate", headers=h, json=_request(ids))).json()
    assert Decimal(res["internal_cost"]) == D("3400")
    assert "gross_margin" in res


# --------------------------------------------------------------------------- #
# Park-fee age boundaries, end to end.
# --------------------------------------------------------------------------- #

async def test_park_fee_bills_child_age_boundaries_correctly(client, admin_tokens):
    """Ages 2/3/11/12 against bounds 3..11: infant, child, child, adult.

    Fees are 70 adult / 40 child / 0 infant over 3 nights:
      (70 x 1 + 40 x 2 + 0 x 1) x 3 = 450
    """
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    req = _request(ids)
    req["travellers"] = [
        {"traveller_type": "child", "age": 2},   # infant
        {"traveller_type": "adult", "age": 3},   # child (lower bound, inclusive)
        {"traveller_type": "adult", "age": 11},  # child (upper bound, inclusive)
        {"traveller_type": "adult", "age": 12},  # adult (just over)
    ]
    res = (await client.post(f"{API}/quotes/calculate", headers=h, json=req)).json()
    park = next(ln for ln in res["lines"] if ln["category"] == "park_fee")
    assert Decimal(park["internal_cost"]) == D("450")
    assert "(A1/C2/I1)" in park["description"]


# --------------------------------------------------------------------------- #
# Degenerate and failure inputs
# --------------------------------------------------------------------------- #

async def test_empty_quote_prices_to_zero(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    req = _request(ids)
    req["legs"] = []
    req["transport"] = []
    res = await client.post(f"{API}/quotes/calculate", headers=h, json=req)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["lines"] == []
    assert Decimal(body["internal_cost"]) == 0
    assert Decimal(body["selling_price"]) == 0
    assert Decimal(body["gross_margin"]) == 0  # guarded division, not an error


async def test_unknown_vehicle_is_404_not_a_free_transfer(client, admin_tokens):
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    req = _request(ids)
    req["transport"] = [{"vehicle_id": str(uuid.uuid4()), "estimated_km": "210", "days": 3}]
    res = await client.post(f"{API}/quotes/calculate", headers=h, json=req)
    assert res.status_code == 404, res.text


async def test_discount_and_tax_flow_through_the_engine(client, admin_tokens):
    """3400 internal -> +25% = 4250 -> -10% = 3825 -> +16% tax = 4437."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    req = _request(ids)
    req["discount_pct"] = "10"
    req["tax_pct"] = "16"
    res = (await client.post(f"{API}/quotes/calculate", headers=h, json=req)).json()
    assert Decimal(res["selling_subtotal"]) == D("4250")
    assert Decimal(res["discount_value"]) == D("425")
    assert Decimal(res["after_discount"]) == D("3825")
    assert Decimal(res["tax"]) == D("612")
    assert Decimal(res["selling_price"]) == D("4437")
    # Invariant holds with discount and tax in play.
    assert Decimal(res["gross_profit"]) == Decimal(res["selling_price"]) - Decimal(
        res["internal_cost"]
    )


async def test_presentation_currency_scales_every_line_by_the_same_rate(client, admin_tokens):
    """A KES quote must be the USD quote x rate, line for line — no drift."""
    h = _h(admin_tokens)
    ids = await _setup(client, h)
    await client.post(
        f"{API}/exchange-rates",
        headers=h,
        json={
            "base_currency": "USD",
            "quote_currency": "KES",
            "rate": "130",
            "effective_from": "2026-01-01",
        },
    )
    usd = (await client.post(f"{API}/quotes/calculate", headers=h, json=_request(ids))).json()
    kes = (
        await client.post(
            f"{API}/quotes/calculate", headers=h, json=_request(ids, currency="KES")
        )
    ).json()
    assert Decimal(kes["internal_cost"]) == Decimal(usd["internal_cost"]) * 130
    assert Decimal(kes["selling_price"]) == Decimal(usd["selling_price"]) * 130
    for u_line, k_line in zip(usd["lines"], kes["lines"], strict=True):
        assert Decimal(k_line["internal_cost"]) == Decimal(u_line["internal_cost"]) * 130
